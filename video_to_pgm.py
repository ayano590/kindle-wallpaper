import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# ------------------------------------------------------------
# Einstellungen
# ------------------------------------------------------------

OUTPUT_WIDTH = 1072
OUTPUT_HEIGHT = 1448


# ------------------------------------------------------------
# FFprobe: Video-Auflösung ermitteln
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
            "Bitte FFmpeg installieren und ffmpeg/ffprobe zum PATH hinzufügen."
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
# Einzelnes RGB-Bild verarbeiten
# ------------------------------------------------------------

def process_frame(rgb_frame, source_width, source_height):
    """
    RGB -> Mittelwert-Grayscale
    Querformat -> 90° drehen
    Auf 1072x1448 einpassen
    Schwarze Ränder hinzufügen
    """

    # RGB-Mittelwert berechnen.
    # Wichtig: nicht die übliche gewichtete Luminanz,
    # sondern tatsächlich (R + G + B) / 3.
    gray = np.mean(rgb_frame, axis=2).astype(np.uint8)

    image = Image.fromarray(gray, mode="L")

    # Querformat erkennen und um 90 Grad drehen.
    if source_width > source_height:
        image = image.rotate(
            90,
            expand=True,
            resample=Image.Resampling.BICUBIC
        )

    # Nach der Rotation haben sich Breite/Höhe geändert.
    image_width, image_height = image.size

    # Maximale Skalierung bestimmen, ohne etwas abzuschneiden.
    scale = min(
        OUTPUT_WIDTH / image_width,
        OUTPUT_HEIGHT / image_height
    )

    new_width = max(1, round(image_width * scale))
    new_height = max(1, round(image_height * scale))

    image = image.resize(
        (new_width, new_height),
        Image.Resampling.LANCZOS
    )

    # Schwarzes Zielbild erzeugen.
    canvas = Image.new(
        "L",
        (OUTPUT_WIDTH, OUTPUT_HEIGHT),
        0
    )

    # Bild mittig platzieren.
    x = (OUTPUT_WIDTH - new_width) // 2
    y = (OUTPUT_HEIGHT - new_height) // 2

    canvas.paste(image, (x, y))

    return canvas


# ------------------------------------------------------------
# PGM speichern
# ------------------------------------------------------------

def save_pgm(image, filename):
    """
    PGM P5, 8-bit.
    """

    pixels = np.asarray(image, dtype=np.uint8)

    with open(filename, "wb") as f:
        f.write(b"P5\n")
        f.write(f"{OUTPUT_WIDTH} {OUTPUT_HEIGHT}\n".encode("ascii"))
        f.write(b"255\n")
        f.write(pixels.tobytes())


# ------------------------------------------------------------
# Video verarbeiten
# ------------------------------------------------------------

def extract_frames(video_path, output_dir, fps):
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    if not video_path.exists():
        raise FileNotFoundError(
            f"Video nicht gefunden: {video_path}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    print("Analysiere Video...")

    source_width, source_height = get_video_info(video_path)

    print(f"Quelle: {source_width} x {source_height}")
    print(f"Ausgabe: {OUTPUT_WIDTH} x {OUTPUT_HEIGHT}")
    print(f"FPS: {fps}")

    if source_width > source_height:
        print("Querformat erkannt -> Bilder werden um 90° gedreht.")

    # FFmpeg gibt genau die gewünschte Anzahl Frames pro Sekunde aus.
    #
    # rgb24:
    #   3 Bytes pro Pixel
    #
    # pipe:1:
    #   Rohdaten direkt an Python senden.
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
            "Bitte FFmpeg installieren und ffmpeg zum PATH hinzufügen."
        )

    frame_size = source_width * source_height * 3
    frame_number = 1

    try:
        while True:
            raw_frame = process.stdout.read(frame_size)

            if len(raw_frame) == 0:
                break

            if len(raw_frame) != frame_size:
                print(
                    "Warnung: Unvollständiger Frame am Ende des Videos.",
                    file=sys.stderr
                )
                break

            # Rohdaten in NumPy-Array umwandeln.
            rgb_frame = np.frombuffer(
                raw_frame,
                dtype=np.uint8
            ).reshape(
                (source_height, source_width, 3)
            )

            # Bild bearbeiten.
            processed = process_frame(
                rgb_frame,
                source_width,
                source_height
            )

            # PGM schreiben.
            filename = output_dir / f"frame{frame_number}.pgm"
            save_pgm(processed, filename)

            print(
                f"\rFrame {frame_number}: {filename.name}",
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
            "FFmpeg konnte das Video nicht vollständig verarbeiten:\n"
            + stderr
        )

    print()
    print(f"Fertig. {frame_number - 1} Frames erzeugt.")
    print(f"Zielordner: {output_dir.resolve()}")


# ------------------------------------------------------------
# Kommandozeile
# ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Video in einzelne 1072x1448 PGM-Grayscale-Frames "
            "umwandeln."
        )
    )

    parser.add_argument(
        "video",
        help="Eingabevideo"
    )

    parser.add_argument(
        "output",
        help="Zielordner für die PGM-Dateien"
    )

    parser.add_argument(
        "--fps",
        type=float,
        default=1.0,
        help="Ausgabe-FPS, z.B. 1, 2, 5 oder 10 (Standard: 1)"
    )

    args = parser.parse_args()

    if args.fps <= 0:
        parser.error("FPS muss größer als 0 sein.")

    try:
        extract_frames(
            args.video,
            args.output,
            args.fps
        )
    except Exception as e:
        print(f"\nFEHLER: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
