/* Copyright (C) 2026 Artur Yakymenko
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Disposable x86_64 UEFI VM fixture: expose only GOP_WIDTH x GOP_HEIGHT,
 * then chainload the unchanged stock GRUB at EFI/BOOT/grub.efi.
 * This changes protocols in RAM, never firmware variables or disk contents.
 * Build with clang --target=x86_64-unknown-windows -ffreestanding
 * -fshort-wchar -mno-red-zone -fno-stack-protector -c, then lld-link
 * /subsystem:efi_application /entry:efi_main /nodefaultlib.
 */

#ifndef GOP_WIDTH
#define GOP_WIDTH 2560
#endif
#ifndef GOP_HEIGHT
#define GOP_HEIGHT 1600
#endif

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef u64 usize;
typedef u64 status;
typedef void *handle;
#define ERROR(n) (0x8000000000000000ULL | (n))
#define INVALID_PARAMETER ERROR(2)
#define UNSUPPORTED ERROR(3)
#define NOT_FOUND ERROR(14)
#define FAILED(s) (((s) >> 63) != 0)

struct guid { u32 a; u16 b, c; u8 d[8]; };
struct header { u64 signature; u32 revision, size, crc, reserved; };
struct boot_services {
    struct header header;
    void *before_allocate_pool[5];
    status (*allocate_pool)(u32, usize, void **);
    status (*free_pool)(void *);
    void *before_handle_protocol[9];
    status (*handle_protocol)(handle, struct guid *, void **);
    void *before_load_image[5];
    status (*load_image)(u8, handle, void *, void *, usize, handle *);
    status (*start_image)(handle, usize *, u16 **);
    void *before_locate_handle_buffer[12];
    status (*locate_handle_buffer)(u32, struct guid *, void *, usize *, handle **);
};
struct system_table {
    struct header header;
    u16 *vendor;
    u32 revision;
    handle console_in_handle;
    void *console_in;
    handle console_out_handle;
    void *console_out;
    handle error_handle;
    void *error_out;
    void *runtime;
    struct boot_services *boot;
};
struct loaded_image {
    u32 revision;
    handle parent;
    struct system_table *system;
    handle device;
};
struct device_path { u8 type, subtype; u16 length; };
struct mode_info {
    u32 version, width, height, format;
    u32 masks[4];
    u32 stride;
};
struct mode {
    u32 max_mode, current;
    struct mode_info *info;
    usize info_size;
    u64 framebuffer;
    usize framebuffer_size;
};
struct gop {
    status (*query)(struct gop *, u32, usize *, struct mode_info **);
    status (*set)(struct gop *, u32);
    status (*blt)(struct gop *, void *, u32, usize, usize, usize, usize,
                  usize, usize, usize);
    struct mode *mode;
};
struct restriction {
    struct gop *gop;
    struct gop original;
    struct mode exposed;
    u32 selected;
};
/* These are x86_64 UEFI ABI offsets, independent of host C library headers. */
_Static_assert(sizeof(void *) == 8, "x86_64 UEFI only");
_Static_assert(__builtin_offsetof(struct system_table, boot) == 96, "system table ABI");
_Static_assert(__builtin_offsetof(struct boot_services, allocate_pool) == 64, "pool ABI");
_Static_assert(__builtin_offsetof(struct boot_services, handle_protocol) == 152, "protocol ABI");
_Static_assert(__builtin_offsetof(struct boot_services, load_image) == 200, "load ABI");
_Static_assert(__builtin_offsetof(struct boot_services, locate_handle_buffer) == 312, "locate ABI");
_Static_assert(__builtin_offsetof(struct loaded_image, device) == 24, "image ABI");
_Static_assert(sizeof(struct mode_info) == 36, "GOP info ABI");
_Static_assert(sizeof(struct mode) == 40, "GOP mode ABI");

static struct boot_services *boot;
static struct restriction restrictions[16];
static usize restriction_count;
static struct guid gop_guid = {
    0x9042a9de, 0x23dc, 0x4a38, {0x96,0xfb,0x7a,0xde,0xd0,0x80,0x51,0x6a}
};
static struct guid loaded_image_guid = {
    0x5b1b31a1, 0x9562, 0x11d2, {0x8e,0x3f,0x00,0xa0,0xc9,0x69,0x72,0x3b}
};
static struct guid device_path_guid = {
    0x09576e91, 0x6d3f, 0x11d2, {0x8e,0x39,0x00,0xa0,0xc9,0x69,0x72,0x3b}
};

static void copy(void *destination, const void *source, usize size)
{
    u8 *out = destination;
    const u8 *in = source;
    while (size--) *out++ = *in++;
}

static struct restriction *lookup(struct gop *gop)
{
    for (usize i = 0; i < restriction_count; i++)
        if (restrictions[i].gop == gop) return &restrictions[i];
    return 0;
}

static status query(struct gop *gop, u32 index, usize *size, struct mode_info **info)
{
    struct restriction *r = lookup(gop);
    if (!r || index || !size || !info) return INVALID_PARAMETER;
    status result = boot->allocate_pool(2, r->exposed.info_size, (void **)info);
    if (FAILED(result)) return result;
    copy(*info, r->exposed.info, r->exposed.info_size);
    *size = r->exposed.info_size;
    return 0;
}

static status set(struct gop *gop, u32 index)
{
    struct restriction *r = lookup(gop);
    if (!r || index) return UNSUPPORTED;
    /* Firmware internals must see their original index and mode structure. */
    struct gop exposed;
    copy(&exposed, gop, sizeof(exposed));
    copy(gop, &r->original, sizeof(*gop));
    status result = r->original.set(gop, r->selected);
    copy(&r->exposed, gop->mode, sizeof(r->exposed));
    r->exposed.max_mode = 1;
    r->exposed.current = 0;
    copy(gop, &exposed, sizeof(*gop));
    return result;
}

static status blt(struct gop *gop, void *buffer, u32 operation,
                  usize sx, usize sy, usize dx, usize dy,
                  usize width, usize height, usize delta)
{
    struct restriction *r = lookup(gop);
    if (!r) return INVALID_PARAMETER;
    struct gop exposed;
    copy(&exposed, gop, sizeof(exposed));
    copy(gop, &r->original, sizeof(*gop));
    status result = r->original.blt(gop, buffer, operation, sx, sy, dx, dy,
                                    width, height, delta);
    copy(gop, &exposed, sizeof(*gop));
    return result;
}

static status restrict_gop(struct gop *gop)
{
    if (lookup(gop)) return 0;
    if (restriction_count == 16) return UNSUPPORTED;
    for (u32 index = 0; index < gop->mode->max_mode; index++) {
        struct mode_info *info = 0;
        usize size = 0;
        status result = gop->query(gop, index, &size, &info);
        if (FAILED(result)) continue;
        int matches = info->width == GOP_WIDTH && info->height == GOP_HEIGHT;
        boot->free_pool(info);
        if (!matches) continue;
        result = gop->set(gop, index);
        if (FAILED(result)) return result;
        struct restriction *r = &restrictions[restriction_count++];
        r->gop = gop;
        copy(&r->original, gop, sizeof(*gop));
        copy(&r->exposed, gop->mode, sizeof(r->exposed));
        r->selected = index;
        r->exposed.max_mode = 1;
        r->exposed.current = 0;
        gop->query = query;
        gop->set = set;
        gop->blt = blt;
        gop->mode = &r->exposed;
        return 0;
    }
    /* Do not silently run with another GOP still exposing fallback modes. */
    return NOT_FOUND;
}

status efi_main(handle image, struct system_table *system)
{
    boot = system->boot;
    struct loaded_image *loaded;
    status result = boot->handle_protocol(image, &loaded_image_guid, (void **)&loaded);
    if (FAILED(result)) return result;
    struct device_path *path;
    result = boot->handle_protocol(loaded->device, &device_path_guid, (void **)&path);
    if (FAILED(result)) return result;
    usize prefix = 0;
    struct device_path *node = path;
    while (node->type != 0x7f) {
        if (node->length < sizeof(*node) || prefix > 65536) return UNSUPPORTED;
        prefix += node->length;
        node = (struct device_path *)((u8 *)path + prefix);
    }
    static const u16 filename[] = L"\\EFI\\BOOT\\grub.efi";
    u8 *chain_path;
    usize file_size = sizeof(struct device_path) + sizeof(filename);
    result = boot->allocate_pool(2, prefix + file_size + sizeof(*node), (void **)&chain_path);
    if (FAILED(result)) return result;
    copy(chain_path, path, prefix);
    node = (struct device_path *)(chain_path + prefix);
    node->type = 4; node->subtype = 4; node->length = (u16)file_size;
    copy(node + 1, filename, sizeof(filename));
    node = (struct device_path *)(chain_path + prefix + file_size);
    node->type = 0x7f; node->subtype = 0xff; node->length = sizeof(*node);
    handle child;
    result = boot->load_image(0, image, chain_path, 0, 0, &child);
    boot->free_pool(chain_path);
    if (FAILED(result)) return result;
    usize count;
    handle *handles;
    result = boot->locate_handle_buffer(2, &gop_guid, 0, &count, &handles);
    if (FAILED(result)) return result;
    for (usize i = 0; i < count; i++) {
        struct gop *gop;
        result = boot->handle_protocol(handles[i], &gop_guid, (void **)&gop);
        if (FAILED(result)) break;
        result = restrict_gop(gop);
        if (FAILED(result)) break;
    }
    boot->free_pool(handles);
    if (!FAILED(result) && restriction_count) result = boot->start_image(child, 0, 0);
    /* If GRUB exits, restore the firmware view before returning to its menu. */
    for (usize i = 0; i < restriction_count; i++)
        copy(restrictions[i].gop, &restrictions[i].original, sizeof(struct gop));
    return result;
}
