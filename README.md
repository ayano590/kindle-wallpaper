# Kindle Wallpaper

A lightweight animated wallpaper engine for the Kindle Paperwhite 3.

The application displays a sequence of PGM images directly through the Kindle framebuffer using **FBInk**, while monitoring the touchscreen for an immediate stop. When the animation stops, the original framebuffer contents are restored.

---

## Features

* Direct framebuffer rendering through `libfbink`
* Automatically discovers all `.pgm` frames in the `frames/` directory
* Numeric frame ordering
* Configurable FPS or frame delay
* Infinite looping
* Touchscreen tap immediately stops the animation
* SIGTERM support for KUAL/stop scripts
* Saves the exact framebuffer contents before starting
* Restores the original framebuffer when stopping
* Full-screen refresh after framebuffer restoration
* Prevents multiple instances from running simultaneously
* Logging to `wallpaper.log`

## Requirements

This project was developed for:

* Kindle Paperwhite 3 (PW3 / 7th generation)
* ARM EABI5 Kindle userspace
* Firmware 5.16.x
* A jailbroken Kindle
* SSH access to the Kindle
* A Linux development machine

The jailbreak itself is **not covered here**.

The Kindle must have:

```text
/dev/fb0 (framebuffer)
/dev/input/event1 (touchscreen device)
```

---

# 1. Installation

Connect your Kindle to your PC via USB.

Create the application directory:

```sh
extensions/kindle-wallpaper
```

Copy the project files into it.

The application directory should look like this:

```text
kindle-wallpaper/
├── kindle-wallpaper
├── wallpaper.conf
├── wallpaper.log
├── wallpaper.pid
├── wallpaper.lock
├── start.sh
├── frames/
│   ├── frame1.pgm
│   ├── frame2.pgm
│   ├── frame3.pgm
│   └── ...
└── lib/
    └── libfbink.so.1.0.0
```

The PID, lock, and log files are generated/managed at runtime.

---

# 2. User Guide

## Wallpaper Frames

Frames are stored in:

```text
frames/
```

Kindle Wallpaper automatically searches this directory for `.pgm` files.

Frames are sorted numerically based on the first number found in their filename.

A filename should contain a numeric frame number. For example:

```text
frame1.pgm
frame2.pgm
frame3.pgm
...
frame10.pgm
frame11.pgm
```

The PW3 framebuffer is:

```text
1072 × 1448
```

The wallpaper frames should ideally be generated at the same resolution.

For this project, PGM frames are used because they map naturally to the Kindle's grayscale framebuffer workflow.

## Animation Configuration

The configuration file is:

```text
wallpaper.conf
```

Example:

```ini
FPS=10
```

Alternatively:

```ini
DELAY_MS=250
```

If both settings are present, `DELAY_MS` takes precedence.

Kindle Wallpaper accepts:

```text
FPS: 0.1 - 60
DELAY_MS: 1 - 60_000
```

## Video to PGM converter

Helper script to generate PGM frames.
Set grayscale levels and optionally Floyd-Steinberg dithering.
Recommended to use low grayscale levels to avoid frame update issues.

Requires:

Python: numpy, pillow

FFmpeg installed + ffmpeg and ffprobe added to PATH

Usage:

```sh
python video-to-pgm.py video.mp4 generated_frames --fps 1 --levels 2 --dither
```

---

# 3. Important Operational Notes

### Exit KOReader first

Kindle Wallpaper is intended to operate without KOReader/Xorg.

### Do not run multiple instances

The application has an internal lock, but avoid manually launching several copies.

### Do not kill it with `SIGKILL`

Avoid:

```sh
kill -9
```

unless absolutely necessary.

`SIGKILL` prevents the application from running its cleanup code and therefore prevents it from restoring the saved framebuffer.

Prefer:

```sh
kill PID
```

which sends `SIGTERM`.

### Keep the FBInk versions matched

Use the same FBInk source version for:

```text
fbink.h
libfbink.so.1.0.0
```

---

# 4. Current Architecture

The project deliberately keeps the wallpaper engine separate from the Kindle framework.

```text
                    Kindle
                      │
             ┌────────┴────────┐
             │                 │
        /dev/fb0        /dev/input/event1
             │                 │
             │                 │
          FBInk             Touch
             │                 │
             └────────┬────────┘
                      │
              Kindle Wallpaper
                      │
              ┌───────┴───────┐
              │               │
           frames/       wallpaper.conf
```

The engine is responsible for:

* loading frames
* timing
* framebuffer access
* rendering
* touch detection
* signal handling
* framebuffer backup/restoration
* cleanup

KUAL is only a launcher/control interface.

---

# 5. Future Extensions

The current architecture can later be extended to support:

* Multiple wallpaper directories
* Multiple KUAL menu entries
* Wallpaper selection
* Per-wallpaper configuration
* Different frame rates
* Different refresh settings
* Pause/resume
* Additional image formats
* Device-specific refresh optimizations
* Optional MTK swipe refresh testing
* Automatic wallpaper selection

The core wallpaper engine should remain independent of these features.

