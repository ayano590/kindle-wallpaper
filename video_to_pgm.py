import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


OUTPUT_WIDTH = 1072
OUTPUT_HEIGHT = 1448


# ------------------------------------------------------------
# FFprobe
# ------------------------------------------------------------

def get_video_info(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(video_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
    except FileNotFoundError:
        raise RuntimeError(
            "FFprobe wurde nicht gefunden. "
            "Bitte FFmpeg installieren."
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Video konnte nicht analysiert werden:\n{e.stderr}"
        )

    data = json.loads(result.stdout)

    if not data.get("streams"):
        raise RuntimeError("Kein Videostream gefunden.")

    stream = data["streams"][0]

    return int(stream["width"]), int(stream["height"])


# ------------------------------------------------------------
# Grayscale
# ------------------------------------------------------------

def rgb_to_gray(rgb_frame):
    """
    Convert RGB to grayscale.

    We intentionally use the same simple average as the original
    project rather than a weighted luma conversion.
    """

    gray = np.mean(
        rgb_frame.astype(np.float32),
        axis=2
    )

    return gray


# ------------------------------------------------------------
# Quantization
# ------------------------------------------------------------

def quantize_gray(gray, levels):
    """
    Reduce the image to a small number of grayscale levels.

    levels=2:
        0, 255

    levels=4:
        0, 85, 170, 255

    levels=16:
        0, 17, 34, ..., 255
    """

    if levels < 2:
        raise ValueError("levels muss mindestens 2 sein.")

    if levels > 256:
        raise ValueError("levels darf höchstens 256 sein.")

    step = 255.0 / (levels - 1)

    quantized = np.round(gray / step) * step

    return np.clip(
        quantized,
        0,
        255
    ).astype(np.uint8)


# ------------------------------------------------------------
# Floyd-Steinberg dithering
# ------------------------------------------------------------

def floyd_steinberg(gray, levels):
    """
    Floyd-Steinberg error diffusion.

    This can make low-level grayscale images look better, but it
    creates high-frequency pixel changes. For eInk video this is
    therefore OPTIONAL and should be tested rather than assumed
    to be better.
    """

    image = gray.astype(np.float32).copy()

    step = 255.0 / (levels - 1)

    height, width = image.shape

    for y in range(height):

        for x in range(width):

            old = image[y, x]

            new = round(old / step) * step

            image[y, x] = new

            error = old - new

            if x + 1 < width:
                image[y, x + 1] += error * 7.0 / 16.0

            if y + 1 < height:

                if x > 0:
                    image[y + 1, x - 1] += \
                        error * 3.0 / 16.0

                image[y + 1, x] += \
                    error * 5.0 / 16.0

                if x + 1 < width:
                    image[y + 1, x + 1] += \
                        error * 1.0 / 16.0

    return np.clip(
        image,
        0,
        255
    ).astype(np.uint8)


# ------------------------------------------------------------
# Image processing
# ------------------------------------------------------------

def process_frame(
    rgb_frame,
    source_width,
    source_height,
    levels,
    dither
):
    """
    RGB
      -> grayscale
      -> rotate if landscape
      -> fit inside 1072x1448
      -> black letterbox
      -> grayscale quantization
    """

    gray = rgb_to_gray(rgb_frame)

    image = Image.fromarray(
        gray.astype(np.uint8),
        mode="L"
    )

    if source_width > source_height:

        image = image.rotate(
            90,
            expand=True,
            resample=Image.Resampling.BICUBIC
        )

    image_width, image_height = image.size

    scale = min(
        OUTPUT_WIDTH / image_width,
        OUTPUT_HEIGHT / image_height
    )

    new_width = max(
        1,
        round(image_width * scale)
    )

    new_height = max(
        1,
        round(image_height * scale)
    )

    image = image.resize(
        (new_width, new_height),
        Image.Resampling.LANCZOS
    )

    canvas = Image.new(
        "L",
        (OUTPUT_WIDTH, OUTPUT_HEIGHT),
        0
    )

    x = (OUTPUT_WIDTH - new_width) // 2
    y = (OUTPUT_HEIGHT - new_height) // 2

    canvas.paste(
        image,
        (x, y)
    )

    canvas_array = np.asarray(
        canvas,
        dtype=np.float32
    )

    if dither:

        canvas_array = floyd_steinberg(
            canvas_array,
            levels
        )

    else:

        canvas_array = quantize_gray(
            canvas_array,
            levels
        )

    return Image.fromarray(
        canvas_array,
        mode="L"
    )


# ------------------------------------------------------------
# PGM
# ------------------------------------------------------------

def save_pgm(image, filename):
    """
    Save binary PGM (P5), 8-bit.
    """

    pixels = np.asarray(
        image,
        dtype=np.uint8
    )

    with open(filename, "wb") as f:

        f.write(b"P5\n")

        f.write(
            f"{OUTPUT_WIDTH} {OUTPUT_HEIGHT}\n"
            .encode("ascii")
        )

        f.write(b"255\n")

        f.write(
            pixels.tobytes()
        )


# ------------------------------------------------------------
# Video processing
# ------------------------------------------------------------

def extract_frames(
    video_path,
    output_dir,
    fps,
    levels,
    dither
):
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    if not video_path.exists():

        raise FileNotFoundError(
            f"Video nicht gefunden: {video_path}"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    print("Analysiere Video...")

    source_width, source_height = \
        get_video_info(video_path)

    print(
        f"Quelle: {source_width} x {source_height}"
    )

    print(
        f"Ausgabe: {OUTPUT_WIDTH} x {OUTPUT_HEIGHT}"
    )

    print(f"FPS: {fps}")
    print(f"Graustufen: {levels}")

    if dither:
        print("Dithering: Floyd-Steinberg")
    else:
        print("Dithering: deaktiviert")

    if source_width > source_height:

        print(
            "Querformat erkannt -> "
            "Bilder werden um 90° gedreht."
        )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",

        "-i", str(video_path),

        "-vf", f"fps={fps}",

        "-f", "rawvideo",
        "-pix_fmt", "rgb24",

        "pipe:1"
    ]

    try:

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    except FileNotFoundError:

        raise RuntimeError(
            "FFmpeg wurde nicht gefunden. "
            "Bitte FFmpeg installieren."
        )

    frame_size = (
        source_width *
        source_height *
        3
    )

    frame_number = 1

    try:

        while True:

            raw_frame = process.stdout.read(
                frame_size
            )

            if len(raw_frame) == 0:
                break

            if len(raw_frame) != frame_size:

                print(
                    "Warnung: Unvollständiger Frame "
                    "am Ende des Videos.",
                    file=sys.stderr
                )

                break

            rgb_frame = np.frombuffer(
                raw_frame,
                dtype=np.uint8
            ).reshape(
                (
                    source_height,
                    source_width,
                    3
                )
            )

            processed = process_frame(
                rgb_frame,
                source_width,
                source_height,
                levels,
                dither
            )

            filename = (
                output_dir /
                f"frame{frame_number}.pgm"
            )

            save_pgm(
                processed,
                filename
            )

            print(
                f"\rFrame {frame_number}: "
                f"{filename.name}",
                end="",
                flush=True
            )

            frame_number += 1

    finally:

        process.stdout.close()

    stderr = process.stderr.read().decode(
        "utf-8",
        errors="replace"
    )

    process.stderr.close()

    return_code = process.wait()

    print()

    if return_code != 0:

        raise RuntimeError(
            "FFmpeg konnte das Video nicht "
            "vollständig verarbeiten:\n"
            + stderr
        )

    print()

    print(
        f"Fertig. {frame_number - 1} "
        f"Frames erzeugt."
    )

    print(
        f"Zielordner: {output_dir.resolve()}"
    )


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Video in Kindle-kompatible "
            "1072x1448 PGM-Grayscale-Frames "
            "umwandeln."
        )
    )

    parser.add_argument(
        "video",
        help="Eingabevideo"
    )

    parser.add_argument(
        "output",
        help="Zielordner für PGM-Dateien"
    )

    parser.add_argument(
        "--fps",
        type=float,
        default=2.5,
        help=(
            "Ausgabe-FPS, z.B. "
            "1, 2, 2.5 oder 5 "
            "(Standard: 2.5)"
        )
    )

    parser.add_argument(
        "--levels",
        type=int,
        default=16,
        choices=[2, 4, 8, 16, 32, 64, 128, 256],
        help=(
            "Anzahl der Graustufen. "
            "Für eInk zunächst 4 oder 16 testen. "
            "(Standard: 16)"
        )
    )

    parser.add_argument(
        "--dither",
        action="store_true",
        help=(
            "Floyd-Steinberg-Dithering verwenden. "
            "Für eInk-Video zunächst deaktiviert lassen."
        )
    )

    args = parser.parse_args()

    if args.fps <= 0:
        parser.error(
            "FPS muss größer als 0 sein."
        )

    try:

        extract_frames(
            args.video,
            args.output,
            args.fps,
            args.levels,
            args.dither
        )

    except Exception as e:

        print(
            f"\nFEHLER: {e}",
            file=sys.stderr
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
