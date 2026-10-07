/* Copyright (C) 2026 Artur Yakymenko
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/uio.h>

/* Socket inspection syscalls are also restricted in these environments. Record
 * only pairs made in this process rather than guessing a descriptor's kind.
 */
#define TRACKED_FDS 65536
static _Atomic unsigned char local_pairs[TRACKED_FDS];

int socketpair(int domain, int type, int protocol, int descriptors[2])
{
    typedef int (*pair_function)(int, int, int, int[2]);
    pair_function original = (pair_function)dlsym(RTLD_NEXT, "socketpair");
    if (!original) {
        errno = ENOSYS;
        return -1;
    }
    int result = original(domain, type, protocol, descriptors);
    if (result == 0 && domain == AF_UNIX && protocol == 0
            && (type & ~(SOCK_CLOEXEC | SOCK_NONBLOCK)) == SOCK_STREAM) {
        for (int i = 0; i < 2; i++)
            if (descriptors[i] >= 0 && descriptors[i] < TRACKED_FDS)
                atomic_store(&local_pairs[descriptors[i]], 1);
    }
    return result;
}

int close(int fd)
{
    typedef int (*close_function)(int);
    close_function original = (close_function)dlsym(RTLD_NEXT, "close");
    if (!original) {
        errno = ENOSYS;
        return -1;
    }
    if (fd >= 0 && fd < TRACKED_FDS)
        atomic_store(&local_pairs[fd], 0);
    return original(fd);
}

/* Test-only compatibility for environments that deny sendto even on an
 * anonymous local socket pair. Keep libalpm's bytes and MSG_NOSIGNAL semantics;
 * only the syscall carrying them changes. No transaction logic is replaced.
 */
__attribute__((constructor)) static void stop_inheriting_transport(void)
{
    /* The runner adds this library only to pacman. Its chrooted hook and other
     * children do not need, and may not contain, the test library.
     */
    unsetenv("LD_PRELOAD");
}

ssize_t send(int fd, const void *buffer, size_t length, int flags)
{
    typedef ssize_t (*send_function)(int, const void *, size_t, int);
    send_function original = (send_function)dlsym(RTLD_NEXT, "send");
    if (!original) {
        errno = ENOSYS;
        return -1;
    }
    ssize_t result = original(fd, buffer, length, flags);
    if (result != -1 || errno != EPERM || flags != MSG_NOSIGNAL
            || fd < 0 || fd >= TRACKED_FDS || !atomic_load(&local_pairs[fd]))
        return result;

    struct iovec data = { .iov_base = (void *)buffer, .iov_len = length };
    struct msghdr message = { .msg_iov = &data, .msg_iovlen = 1 };
    return sendmsg(fd, &message, flags);
}
