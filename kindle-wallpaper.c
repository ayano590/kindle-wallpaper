#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <dirent.h>
#include <poll.h>
#include <ctype.h>
#include <dlfcn.h>
#include <limits.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/file.h>
#include <linux/fb.h>
#include <linux/input.h>

#include "fbink.h"

#define BASE        "/mnt/us/extensions/kindle-wallpaper"
#define FRAME_DIR   BASE "/frames"
#define LOG_FILE    BASE "/wallpaper.log"
#define CONFIG_FILE BASE "/wallpaper.conf"
#define PID_FILE    BASE "/wallpaper.pid"
#define LOCK_FILE   BASE "/wallpaper.lock"
#define TOUCH_DEV   "/dev/input/event1"
#define LIBFBINK    BASE "/lib/libfbink.so.1.0.0"

#define DEFAULT_DELAY_MS 250
#define MIN_FPS          0.1
#define MAX_FPS          60.0
#define MAX_DELAY_MS     60000

static volatile sig_atomic_t running = 1;

static void *fbink_handle = NULL;

static int (*fbink_open_fn)(void) = NULL;
static int (*fbink_close_fn)(int) = NULL;
static int (*fbink_init_fn)(int, FBInkConfig *) = NULL;
static int (*fbink_print_image_fn)(
    int,
    const char *,
    short,
    short,
    FBInkConfig *
) = NULL;
static int (*fbink_refresh_fn)(
    int,
    unsigned int,
    unsigned int,
    unsigned int,
    unsigned int,
    FBInkConfig *
) = NULL;

static int frame_delay_ms = DEFAULT_DELAY_MS;

static int touch_fd = -1;
static int fb_fd = -1;
static int lock_fd = -1;

static char **frames = NULL;
static size_t frame_count = 0;
static size_t frame_capacity = 0;

static void *framebuffer_map = NULL;
static void *saved_framebuffer = NULL;
static size_t framebuffer_size = 0;


/* --------------------------------------------------------- */
/* Signal handling                                            */
/* --------------------------------------------------------- */

static void handle_signal(int sig)
{
    (void)sig;
    running = 0;
}


/* --------------------------------------------------------- */
/* Logging                                                     */
/* --------------------------------------------------------- */

static void log_message(const char *message)
{
    FILE *file = fopen(LOG_FILE, "a");

    if (file) {
        fprintf(file, "%s\n", message);
        fclose(file);
    }

    printf("%s\n", message);
    fflush(stdout);
}


/* --------------------------------------------------------- */
/* Configuration                                               */
/* --------------------------------------------------------- */

static int parse_double(const char *text, double *value)
{
    char *end;
    double result;

    errno = 0;
    result = strtod(text, &end);

    while (isspace((unsigned char)*end))
        end++;

    if (errno != 0 || end == text || *end != '\0')
        return -1;

    *value = result;

    return 0;
}


static int parse_int(const char *text, int *value)
{
    char *end;
    long result;

    errno = 0;
    result = strtol(text, &end, 10);

    while (isspace((unsigned char)*end))
        end++;

    if (errno != 0 ||
        end == text ||
        *end != '\0' ||
        result < 1 ||
        result > INT_MAX) {

        return -1;
    }

    *value = (int)result;

    return 0;
}


static void load_config(void)
{
    FILE *file = fopen(CONFIG_FILE, "r");

    if (!file)
        return;

    char line[256];

    double configured_fps = 0.0;
    int configured_delay = 0;

    int have_fps = 0;
    int have_delay = 0;

    while (fgets(line, sizeof(line), file)) {

        char *p = line;

        while (isspace((unsigned char)*p))
            p++;

        if (*p == '#' || *p == '\0')
            continue;

        if (strncmp(p, "FPS=", 4) == 0) {

            double fps;

            if (parse_double(p + 4, &fps) == 0 &&
                fps >= MIN_FPS &&
                fps <= MAX_FPS) {

                configured_fps = fps;
                have_fps = 1;
            }

        } else if (strncmp(p, "DELAY_MS=", 9) == 0) {

            int delay;

            if (parse_int(p + 9, &delay) == 0 &&
                delay <= MAX_DELAY_MS) {

                configured_delay = delay;
                have_delay = 1;
            }
        }
    }

    fclose(file);

    /*
     * DELAY_MS takes precedence if both settings are present.
     */
    if (have_delay) {

        frame_delay_ms = configured_delay;

    } else if (have_fps) {

        frame_delay_ms = (int)(1000.0 / configured_fps);

        if (frame_delay_ms < 1)
            frame_delay_ms = 1;
    }
}


/* --------------------------------------------------------- */
/* Frame sorting                                               */
/* --------------------------------------------------------- */

static long frame_number(const char *path)
{
    const char *name = strrchr(path, '/');
    const char *p = name ? name + 1 : path;

    while (*p && !isdigit((unsigned char)*p))
        p++;

    if (!*p)
        return -1;

    char *end;

    errno = 0;

    long number = strtol(p, &end, 10);

    if (errno != 0 || end == p)
        return -1;

    return number;
}


static int compare_frames(const void *a, const void *b)
{
    const char *fa = *(const char * const *)a;
    const char *fb = *(const char * const *)b;

    long na = frame_number(fa);
    long nb = frame_number(fb);

    if (na < nb)
        return -1;

    if (na > nb)
        return 1;

    return strcmp(fa, fb);
}


/* --------------------------------------------------------- */
/* Frame loading                                               */
/* --------------------------------------------------------- */

static int add_frame(const char *path)
{
    if (frame_count == frame_capacity) {

        size_t new_capacity =
            frame_capacity ? frame_capacity * 2 : 32;

        char **new_frames =
            realloc(frames, new_capacity * sizeof(*frames));

        if (!new_frames)
            return -1;

        frames = new_frames;
        frame_capacity = new_capacity;
    }

    frames[frame_count] = strdup(path);

    if (!frames[frame_count])
        return -1;

    frame_count++;

    return 0;
}


static int load_frames(void)
{
    DIR *dir = opendir(FRAME_DIR);

    if (!dir) {

        fprintf(stderr,
                "Cannot open frame directory %s: %s\n",
                FRAME_DIR,
                strerror(errno));

        return -1;
    }

    struct dirent *entry;

    while ((entry = readdir(dir)) != NULL) {

        const char *name = entry->d_name;

        if (name[0] == '.')
            continue;

        size_t name_length = strlen(name);

        if (name_length < 4)
            continue;

        if (strcasecmp(name + name_length - 4, ".pgm") != 0)
            continue;

        if (frame_number(name) < 0)
            continue;

        size_t path_length =
            strlen(FRAME_DIR) + 1 + name_length + 1;

        char *path = malloc(path_length);

        if (!path) {
            closedir(dir);
            return -1;
        }

        snprintf(
            path,
            path_length,
            "%s/%s",
            FRAME_DIR,
            name
        );

        int result = add_frame(path);

        free(path);

        if (result < 0) {
            closedir(dir);
            return -1;
        }
    }

    closedir(dir);

    if (frame_count == 0) {

        fprintf(stderr,
                "No PGM frames found in %s\n",
                FRAME_DIR);

        return -1;
    }

    qsort(
        frames,
        frame_count,
        sizeof(*frames),
        compare_frames
    );

    return 0;
}


/* --------------------------------------------------------- */
/* FBInk                                                       */
/* --------------------------------------------------------- */

static int load_fbink(void)
{
    fbink_handle = dlopen(LIBFBINK, RTLD_NOW);

    if (!fbink_handle) {

        fprintf(stderr,
                "Unable to load %s: %s\n",
                LIBFBINK,
                dlerror());

        return -1;
    }

    fbink_open_fn =
        (int (*)(void))
        dlsym(fbink_handle, "fbink_open");

    fbink_close_fn =
        (int (*)(int))
        dlsym(fbink_handle, "fbink_close");

    fbink_init_fn =
        (int (*)(int, FBInkConfig *))
        dlsym(fbink_handle, "fbink_init");

    fbink_print_image_fn =
        (int (*)(int, const char *, short, short, FBInkConfig *))
        dlsym(fbink_handle, "fbink_print_image");

    fbink_refresh_fn =
        (int (*)(int, unsigned int, unsigned int,
                 unsigned int, unsigned int, FBInkConfig *))
        dlsym(fbink_handle, "fbink_refresh");

    if (!fbink_open_fn ||
        !fbink_close_fn ||
        !fbink_init_fn ||
        !fbink_print_image_fn ||
        !fbink_refresh_fn) {

        fprintf(stderr,
                "Required FBInk functions not found.\n");

        dlclose(fbink_handle);
        fbink_handle = NULL;

        return -1;
    }

    return 0;
}


/* --------------------------------------------------------- */
/* Single-instance lock                                        */
/* --------------------------------------------------------- */

static int acquire_lock(void)
{
    lock_fd = open(
        LOCK_FILE,
        O_RDWR | O_CREAT,
        0644
    );

    if (lock_fd < 0) {

        fprintf(stderr,
                "Cannot open lock file: %s\n",
                strerror(errno));

        return -1;
    }

    if (flock(lock_fd, LOCK_EX | LOCK_NB) < 0) {

        if (errno == EWOULDBLOCK) {

            fprintf(stderr,
                    "Kindle Wallpaper is already running.\n");

        } else {

            fprintf(stderr,
                    "Cannot acquire wallpaper lock: %s\n",
                    strerror(errno));
        }

        close(lock_fd);
        lock_fd = -1;

        return -1;
    }

    return 0;
}


/* --------------------------------------------------------- */
/* Framebuffer backup                                          */
/* --------------------------------------------------------- */

static int save_framebuffer(void)
{
    struct fb_fix_screeninfo fix;

    if (ioctl(fb_fd, FBIOGET_FSCREENINFO, &fix) < 0) {

        fprintf(stderr,
                "FBIOGET_FSCREENINFO failed: %s\n",
                strerror(errno));

        return -1;
    }

    if (fix.smem_len == 0) {

        fprintf(stderr,
                "Framebuffer reports zero size.\n");

        return -1;
    }

    framebuffer_size = fix.smem_len;

    framebuffer_map = mmap(
        NULL,
        framebuffer_size,
        PROT_READ | PROT_WRITE,
        MAP_SHARED,
        fb_fd,
        0
    );

    if (framebuffer_map == MAP_FAILED) {

        fprintf(stderr,
                "Framebuffer mmap failed: %s\n",
                strerror(errno));

        framebuffer_map = NULL;
        framebuffer_size = 0;

        return -1;
    }

    saved_framebuffer = malloc(framebuffer_size);

    if (!saved_framebuffer) {

        fprintf(stderr,
                "Unable to allocate framebuffer backup.\n");

        munmap(
            framebuffer_map,
            framebuffer_size
        );

        framebuffer_map = NULL;
        framebuffer_size = 0;

        return -1;
    }

    memcpy(
        saved_framebuffer,
        framebuffer_map,
        framebuffer_size
    );

    return 0;
}


static void restore_framebuffer(FBInkConfig *config)
{
    if (!saved_framebuffer ||
        !framebuffer_map ||
        framebuffer_size == 0) {

        return;
    }

    log_message("Restoring original framebuffer.");

    memcpy(
        framebuffer_map,
        saved_framebuffer,
        framebuffer_size
    );

    int ret = fbink_refresh_fn(
        fb_fd,
        0,
        0,
        0,
        0,
        config
    );

    if (ret < 0) {

        fprintf(stderr,
                "FBInk framebuffer refresh failed: %d\n",
                ret);
    }

    free(saved_framebuffer);
    saved_framebuffer = NULL;

    munmap(
        framebuffer_map,
        framebuffer_size
    );

    framebuffer_map = NULL;
    framebuffer_size = 0;
}


/* --------------------------------------------------------- */
/* Touchscreen                                                  */
/* --------------------------------------------------------- */

static int open_touchscreen(void)
{
    touch_fd = open(
        TOUCH_DEV,
        O_RDONLY | O_NONBLOCK
    );

    if (touch_fd < 0) {

        fprintf(stderr,
                "Cannot open %s: %s\n",
                TOUCH_DEV,
                strerror(errno));

        return -1;
    }

    return 0;
}


static void drain_touch_events(void)
{
    struct input_event event;

    while (read(touch_fd,
                &event,
                sizeof(event)) ==
           (ssize_t)sizeof(event)) {
    }
}


static int wait_for_frame(void)
{
    struct pollfd pfd = {
        .fd = touch_fd,
        .events = POLLIN,
        .revents = 0
    };

    int result = poll(
        &pfd,
        1,
        frame_delay_ms
    );

    if (result < 0) {

        if (errno == EINTR)
            return 0;

        fprintf(stderr,
                "Touchscreen poll failed: %s\n",
                strerror(errno));

        return 1;
    }

    if (result == 0)
        return 0;

    if (pfd.revents & POLLIN) {

        struct input_event event;

        while (read(touch_fd,
                    &event,
                    sizeof(event)) ==
               (ssize_t)sizeof(event)) {

            if (event.type == EV_KEY &&
                event.code == BTN_TOUCH &&
                event.value == 1) {

                return 1;
            }
        }
    }

    if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {

        fprintf(stderr,
                "Touchscreen device error.\n");

        return 1;
    }

    return 0;
}


/* --------------------------------------------------------- */
/* Cleanup                                                     */
/* --------------------------------------------------------- */

static void cleanup(FBInkConfig *config)
{
    if (saved_framebuffer)
        restore_framebuffer(config);

    if (touch_fd >= 0) {

        close(touch_fd);
        touch_fd = -1;
    }

    if (fb_fd >= 0 &&
        fbink_close_fn) {

        fbink_close_fn(fb_fd);
        fb_fd = -1;
    }

    if (fbink_handle) {

        dlclose(fbink_handle);
        fbink_handle = NULL;
    }

    for (size_t i = 0; i < frame_count; i++)
        free(frames[i]);

    free(frames);

    frames = NULL;
    frame_count = 0;
    frame_capacity = 0;

    if (lock_fd >= 0) {

        flock(lock_fd, LOCK_UN);
        close(lock_fd);
        lock_fd = -1;
    }
}


/* --------------------------------------------------------- */
/* Main                                                         */
/* --------------------------------------------------------- */

int main(void)
{
    printf("Kindle Wallpaper Controller\n");

    struct sigaction action;

    memset(&action, 0, sizeof(action));
    action.sa_handler = handle_signal;

    sigemptyset(&action.sa_mask);

    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);

    if (acquire_lock() < 0)
        return 1;

    load_config();

    printf("Frame delay: %d ms\n",
           frame_delay_ms);

    if (load_frames() < 0) {

        cleanup(NULL);
        return 1;
    }

    printf("Frames found: %lu\n",
           (unsigned long)frame_count);

    if (load_fbink() < 0) {

        cleanup(NULL);
        return 1;
    }

    fb_fd = fbink_open_fn();

    if (fb_fd < 0) {

        fprintf(stderr,
                "fbink_open failed: %d\n",
                fb_fd);

        cleanup(NULL);
        return 1;
    }

    FBInkConfig config = {0};

    int ret = fbink_init_fn(
        fb_fd,
        &config
    );

    if (ret < 0) {

        fprintf(stderr,
                "fbink_init failed: %d\n",
                ret);

        cleanup(&config);
        return 1;
    }

    if (save_framebuffer() < 0) {

        cleanup(&config);
        return 1;
    }

    if (open_touchscreen() < 0) {

        cleanup(&config);
        return 1;
    }

    drain_touch_events();

    log_message("Kindle Wallpaper started.");

    size_t frame = 0;

    while (running) {

        ret = fbink_print_image_fn(
            fb_fd,
            frames[frame],
            0,
            0,
            &config
        );

        if (ret < 0) {

            fprintf(stderr,
                    "FBInk failed on frame %lu: %d\n",
                    (unsigned long)(frame + 1),
                    ret);
        }

        if (wait_for_frame()) {

            if (running)
                log_message("Touch detected. Stopping.");

            break;
        }

        frame++;

        if (frame >= frame_count)
            frame = 0;
    }

    log_message("Exiting Kindle Wallpaper...");

    unlink(PID_FILE);

    cleanup(&config);

    log_message("Stopped Kindle Wallpaper.");

    return 0;
}

