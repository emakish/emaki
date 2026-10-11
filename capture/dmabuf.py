# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read compositor DMA-BUF images through EGL without assuming a linear layout."""
import ctypes as C
import ctypes.util
import fcntl
import threading

DRM_FORMAT_MOD_INVALID = (1 << 56) - 1
EGL_NONE = 0x3038
EGL_WIDTH, EGL_HEIGHT = 0x3057, 0x3056
EGL_LINUX_DRM_FOURCC_EXT = 0x3271
EGL_DMA_BUF_PLANE0_FD_EXT = 0x3272
EGL_DMA_BUF_PLANE0_OFFSET_EXT = 0x3273
EGL_DMA_BUF_PLANE0_PITCH_EXT = 0x3274
EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT = 0x3443
EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT = 0x3444
EGL_LINUX_DMA_BUF_EXT = 0x3270
GL_TEXTURE_2D, GL_FRAMEBUFFER = 0x0DE1, 0x8D40
GL_COLOR_ATTACHMENT0, GL_FRAMEBUFFER_COMPLETE = 0x8CE0, 0x8CD5
GL_RGBA, GL_UNSIGNED_BYTE = 0x1908, 0x1401
MAX_DIMENSION = 16384
MAX_BYTES = 256 * 1024 * 1024
PACKED_FORMATS = {int.from_bytes(name, 'little') for name in (b'XR24', b'AR24', b'XB24', b'AB24')}


class CaptureImportError(RuntimeError):
    pass


def image_attributes(width, height, fourcc, modifier, planes):
    """Validate packed-image planes and construct EGLint image attributes.

    INVALID means the driver owns the layout. Omitting both modifier attributes
    is essential: explicitly claiming LINEAR can corrupt pixels or fail import.
    The caller keeps the borrowed descriptor and its source buffer alive.
    """
    integers = (width, height, fourcc, modifier)
    if any(type(value) is not int for value in integers):
        raise CaptureImportError('Invalid DMA-BUF metadata')
    if (not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION or
            width * height * 4 > MAX_BYTES or fourcc not in PACKED_FORMATS or
            not 0 <= modifier < 1 << 64 or not 1 <= len(planes) <= 4):
        raise CaptureImportError('Unsupported DMA-BUF geometry or format')
    attributes = [EGL_WIDTH, width, EGL_HEIGHT, height,
                  EGL_LINUX_DRM_FOURCC_EXT, fourcc]
    for index, plane in enumerate(planes):
        if len(plane) != 3:
            raise CaptureImportError('Invalid DMA-BUF plane')
        fd, offset, stride = plane
        if (any(type(value) is not int for value in (fd, offset, stride)) or
                not 0 <= fd < 1 << 31 or not 0 <= offset < 1 << 31 or
                not (width * 4 if index == 0 else 1) <= stride < 1 << 31 or
                offset + stride * (height - 1) + width * 4 >= 1 << 63):
            raise CaptureImportError('Invalid DMA-BUF plane')
        try:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError as error:
            raise CaptureImportError('Closed DMA-BUF descriptor') from error
        # The fourth plane was added with a different enum range.
        fd_attribute = EGL_DMA_BUF_PLANE0_FD_EXT + 3 * index if index < 3 else 0x3440
        attributes.extend((fd_attribute, fd, fd_attribute + 1, offset, fd_attribute + 2, stride))
        if modifier != DRM_FORMAT_MOD_INVALID:
            attributes.extend((EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT + 2 * index, modifier & 0xffffffff,
                               EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT + 2 * index, modifier >> 32))
    attributes.append(EGL_NONE)
    return attributes


class _Api:
    def __init__(self):
        egl_name, gl_name = C.util.find_library('EGL'), C.util.find_library('GLESv2')
        if not egl_name or not gl_name:
            raise CaptureImportError('EGL or OpenGL ES is unavailable')
        try:
            self.egl, self.gl = C.CDLL(egl_name), C.CDLL(gl_name)
        except OSError as error:
            raise CaptureImportError('Cannot load EGL or OpenGL ES') from error
        ptr, integer, uint = C.c_void_p, C.c_int, C.c_uint
        pint, puint = C.POINTER(integer), C.POINTER(uint)
        self.bind('eglGetProcAddress', ptr, [C.c_char_p])
        self.bind('eglGetDisplay', ptr, [ptr])
        self.bind('eglInitialize', uint, [ptr, pint, pint])
        self.bind('eglQueryString', C.c_char_p, [ptr, integer])
        self.bind('eglBindAPI', uint, [uint])
        self.bind('eglChooseConfig', uint, [ptr, pint, C.POINTER(ptr), integer, pint])
        self.bind('eglCreateContext', ptr, [ptr, ptr, ptr, pint])
        self.bind('eglCreatePbufferSurface', ptr, [ptr, ptr, pint])
        self.bind('eglMakeCurrent', uint, [ptr, ptr, ptr, ptr])
        self.bind('eglDestroyContext', uint, [ptr, ptr])
        self.bind('eglDestroySurface', uint, [ptr, ptr])
        self.bind('eglTerminate', uint, [ptr])
        self.bind('eglGetError', uint, [])
        self.bind('glGetError', uint, [], gl=True)
        self.bind('glGetString', C.c_char_p, [uint], gl=True)
        self.bind('glGenTextures', None, [integer, puint], gl=True)
        self.bind('glDeleteTextures', None, [integer, puint], gl=True)
        self.bind('glBindTexture', None, [uint, uint], gl=True)
        self.bind('glTexParameteri', None, [uint, uint, integer], gl=True)
        self.bind('glGenFramebuffers', None, [integer, puint], gl=True)
        self.bind('glDeleteFramebuffers', None, [integer, puint], gl=True)
        self.bind('glBindFramebuffer', None, [uint, uint], gl=True)
        self.bind('glFramebufferTexture2D', None, [uint, uint, uint, uint, integer], gl=True)
        self.bind('glCheckFramebufferStatus', uint, [uint], gl=True)
        self.bind('glPixelStorei', None, [uint, integer], gl=True)
        self.bind('glReadPixels', None, [integer, integer, integer, integer, uint, uint, ptr], gl=True)

    def bind(self, name, result, arguments, gl=False):
        function = getattr(self.gl if gl else self.egl, name)
        function.restype, function.argtypes = result, arguments
        setattr(self, name, function)

    def extension(self, name, result, arguments):
        address = self.eglGetProcAddress(name.encode('ascii'))
        if not address:
            raise CaptureImportError('Missing graphics function: ' + name)
        return C.CFUNCTYPE(result, *arguments)(address)


def _ints(values):
    return (C.c_int * len(values))(*values)


class Importer:
    """A private offscreen GLES context; never connects to the desktop session.

    Context ownership is released after every read so final cleanup can run on
    the controlling thread once the sample callback has finished. Drivers must
    support rendering imported packed images; external-texture-only modifiers
    fail closed rather than being read as another format.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._closed = False
        self._api = None
        self._display = self._context = self._surface = None
        self._initialized = False
        self._modifiers = False

    def _initialize(self):
        api = self._api = _Api()
        # Mesa's surfaceless platform selects the default render device without
        # opening a Wayland display or creating any visible surface.
        extensions = (api.eglQueryString(None, 0x3055) or b'').split()
        if b'EGL_MESA_platform_surfaceless' in extensions:
            platform_display = api.extension('eglGetPlatformDisplayEXT', C.c_void_p,
                                             [C.c_uint, C.c_void_p, C.POINTER(C.c_int)])
            self._display = platform_display(0x31DD, None, None)
        else:
            raise CaptureImportError('Offscreen EGL platform is unavailable')
        major, minor = C.c_int(), C.c_int()
        if not self._display or not api.eglInitialize(self._display, C.byref(major), C.byref(minor)):
            raise CaptureImportError('Cannot initialize offscreen EGL')
        self._initialized = True
        extensions = (api.eglQueryString(self._display, 0x3055) or b'').split()
        if b'EGL_EXT_image_dma_buf_import' not in extensions or b'EGL_KHR_image_base' not in extensions:
            raise CaptureImportError('EGL DMA-BUF image import is unavailable')
        self._modifiers = b'EGL_EXT_image_dma_buf_import_modifiers' in extensions
        self._create_image = api.extension('eglCreateImageKHR', C.c_void_p,
            [C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p, C.POINTER(C.c_int)])
        self._destroy_image = api.extension('eglDestroyImageKHR', C.c_uint,
                                            [C.c_void_p, C.c_void_p])
        self._target_image = api.extension('glEGLImageTargetTexture2DOES', None,
                                           [C.c_uint, C.c_void_p])
        if not api.eglBindAPI(0x30A0):  # EGL_OPENGL_ES_API
            raise CaptureImportError('Cannot select OpenGL ES')
        config, count = C.c_void_p(), C.c_int()
        attributes = _ints([0x3033, 1, 0x3040, 4, 0x3024, 8, 0x3023, 8, 0x3022, 8,
                            0x3021, 8, EGL_NONE])  # PBUFFER_BIT, OPENGL_ES2_BIT, RGBA8
        if not api.eglChooseConfig(self._display, attributes, C.byref(config), 1, C.byref(count)) or not count.value:
            raise CaptureImportError('No offscreen OpenGL ES configuration')
        self._context = api.eglCreateContext(self._display, config, None, _ints([0x3098, 2, EGL_NONE]))
        self._surface = api.eglCreatePbufferSurface(self._display, config,
                                                   _ints([EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE]))
        if not self._context or not self._surface:
            raise CaptureImportError('Cannot create offscreen OpenGL ES context')

    def read_frame(self, width, height, fourcc, modifier, planes):
        attributes = image_attributes(width, height, fourcc, modifier, planes)
        with self._lock:
            if self._closed:
                raise CaptureImportError('DMA-BUF importer is closed')
            if self._api is None:
                try:
                    self._initialize()
                except Exception:
                    self._dispose()
                    self._closed = True
                    raise
            api = self._api
            if modifier != DRM_FORMAT_MOD_INVALID and not self._modifiers:
                raise CaptureImportError('Explicit DMA-BUF modifiers are unavailable')
            if not api.eglMakeCurrent(self._display, self._surface, self._surface, self._context):
                raise CaptureImportError('Cannot activate OpenGL ES context')
            image = None
            texture, framebuffer = C.c_uint(), C.c_uint()
            try:
                if b'GL_OES_EGL_image' not in (api.glGetString(0x1F03) or b'').split():
                    raise CaptureImportError('OpenGL ES EGL image import is unavailable')
                image = self._create_image(self._display, None, EGL_LINUX_DMA_BUF_EXT, None,
                                           _ints(attributes))
                if not image:
                    raise CaptureImportError('Cannot import compositor DMA-BUF image')
                api.glGenTextures(1, C.byref(texture))
                api.glBindTexture(GL_TEXTURE_2D, texture)
                api.glTexParameteri(GL_TEXTURE_2D, 0x2801, 0x2600)  # MIN_FILTER, NEAREST
                api.glTexParameteri(GL_TEXTURE_2D, 0x2800, 0x2600)  # MAG_FILTER, NEAREST
                self._target_image(GL_TEXTURE_2D, image)
                api.glGenFramebuffers(1, C.byref(framebuffer))
                api.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer)
                api.glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0)
                if (api.glGetError() or
                        api.glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE):
                    raise CaptureImportError('Imported compositor image is not readable')
                pixels = (C.c_ubyte * (width * height * 4))()
                api.glPixelStorei(0x0D05, 1)  # GL_PACK_ALIGNMENT
                # Direct attachment preserves buffer row order; no draw or
                # texture-coordinate transform is introduced here.
                api.glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, pixels)
                if api.glGetError():
                    raise CaptureImportError('Cannot read compositor image pixels')
                return bytes(pixels)
            finally:
                api.glBindFramebuffer(GL_FRAMEBUFFER, 0)
                if framebuffer.value:
                    api.glDeleteFramebuffers(1, C.byref(framebuffer))
                api.glBindTexture(GL_TEXTURE_2D, 0)
                if texture.value:
                    api.glDeleteTextures(1, C.byref(texture))
                if image:
                    self._destroy_image(self._display, image)
                api.eglMakeCurrent(self._display, None, None, None)

    def _dispose(self):
        if self._api and self._display:
            if self._surface:
                self._api.eglDestroySurface(self._display, self._surface)
            if self._context:
                self._api.eglDestroyContext(self._display, self._context)
            if self._initialized:
                self._api.eglTerminate(self._display)
        self._display = self._surface = self._context = None
        self._initialized = False

    def close(self):
        with self._lock:
            self._dispose()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
