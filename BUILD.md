# 1. Development Machine Setup

The project uses an ARM cross-compiler because the Kindle is an ARM device.

The compiler used during development was:

```text
arm-kindlepw2-linux-gnueabi-gcc
```

## Build FBInk

FBInk is used as the Kindle framebuffer/e-ink interface.

Clone the repository:

```sh
git clone https://github.com/NiLuJe/FBInk.git
cd FBInk
```

Initialize the required submodules:

```sh
git submodule update --init --recursive
```

Build the Kindle image-capable version:

```sh
make kindle KINDLE=1 IMAGE=1 CROSS_TC=arm-kindlepw2-linux-gnueabi
```

The resulting executable is:

```text
Release/fbink
```

The shared library is also produced by the build.

## Verify the FBInk Build

Check the resulting binary:

```sh
file Release/fbink
```

It should be an ARM EABI binary suitable for the Kindle.

The image-capable build supports formats including:

* PNG
* JPEG
* TGA
* BMP
* GIF
* PNM/PGM

For this project, PGM is used for animation frames.

## FBInk API Used by Kindle Wallpaper

Kindle Wallpaper loads the FBInk shared library with `dlopen()` and obtains the required functions using `dlsym()`.

The application uses:

```c
fbink_open()
fbink_close()
fbink_init()
fbink_print_image()
fbink_refresh()
```

The FBInk configuration is initialized with:

```c
FBInkConfig config = {0};
```

FBInk's configuration structure is explicitly designed to be safely zero-initialized.

## Building Kindle Wallpaper

The source file is:

```text
kindle-wallpaper.c
```

It requires the FBInk header:

```text
fbink.h
```

from the FBInk source tree.

A typical cross-compilation command is:

```sh
arm-kindlepw2-linux-gnueabi-gcc \
    -O2 \
    -Wall \
    -Wextra \
    -I/path/to/FBInk \
    kindle-wallpaper.c \
    -o kindle-wallpaper \
    -ldl
    -lrt
```

The exact FBInk include path should point at the `fbink.h` belonging to the same FBInk version used to build `libfbink.so.1.0.0`.

The `-ldl` option is required because Kindle Wallpaper uses:

```c
dlopen()
dlsym()
dlclose()
```

The `-lrt` option is required for old Kindle PW2 toolchains because `clock_gettime()` lives in `librt`.

---

## Important: Use the Matching FBInk Header

Do not mix an unrelated `fbink.h` with the shared library.

The header defines types such as:

```c
FBInkConfig
```

and the function declarations used by the application.

The header and shared library should come from the same FBInk source/build.

---

## FBInk `is_animated`

The FBInk configuration contains:

```c
bool is_animated;
```

This should **not** be confused with Kindle Wallpaper's frame animation.

`is_animated` enables an MTK-specific e-ink refresh animation configured through:

```c
fbink_mtk_set_swipe_data()
```

The FBInk implementation sets:

```c
update.swipe_data = mtkSwipeData;
update.waveform_mode = WAVEFORM_MODE_AUTO;
update.update_mode = UPDATE_MODE_PARTIAL;
update.flags |= MTK_EPDC_FLAG_ENABLE_SWIPE;
```

when the option is enabled.

This is an animation performed by the Kindle MTK display driver during an individual refresh.

For the PW3 implementation, `is_animated` should remain disabled unless the relevant FBInk/Kindle driver path has been verified for the specific device.

---

# 2. Minimal Installation Checklist

For a fresh installation, the essential steps are:

```text
[ ] Kindle already jailbroken
[ ] ARM Kindle cross-compiler installed
[ ] FBInk cloned
[ ] FBInk submodules initialized
[ ] FBInk built with Kindle + IMAGE support
[ ] kindle-wallpaper binary cross-compiled
[ ] files copied to Kindle
[ ] kindle-wallpaper started
[ ] touch tested
[ ] framebuffer restoration verified
```

