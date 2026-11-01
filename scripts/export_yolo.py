"""Export YOLO11n once, on a laptop, into formats the Raspberry Pi can run on CPU.

This is the only code that needs Ultralytics and PyTorch. Install them in a separate
environment, the Pi does not need them:

    python -m venv .venv-export
    .venv-export\\Scripts\\pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    .venv-export\\Scripts\\pip install ultralytics onnx onnxslim pnnx ncnn
    .venv-export\\Scripts\\python scripts/export_yolo.py

The exported files keep a fixed input size, so each size is exported separately.
Output goes to models/yolo/ (git-ignored, see docs/DECISIONS.md for why).
"""

import argparse
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="yolo11n", help="Model name (default: yolo11n)")
    parser.add_argument(
        "--imgsz",
        type=int,
        nargs="+",
        default=[320, 640],
        help="Input sizes to export, multiples of 32 (default: 320 640)",
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        choices=["ncnn", "onnx"],
        default=["ncnn", "onnx"],
        help="Formats to export (default: ncnn onnx)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "models" / "yolo",
        help="Output directory (default: models/yolo)",
    )
    return parser.parse_args()


def export_one(model_name: str, imgsz: int, fmt: str, output: Path) -> Path:
    from ultralytics import YOLO

    # Fresh model per export: exporting mutates the loaded network
    model = YOLO(f"{model_name}.pt")
    exported = Path(
        model.export(format=fmt, imgsz=imgsz, half=False, device="cpu", nms=False)
    )
    if fmt == "ncnn":
        target = output / f"{model_name}_{imgsz}_ncnn_model"
    else:
        target = output / f"{model_name}_{imgsz}.onnx"
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    shutil.move(str(exported), str(target))
    return target


def main() -> int:
    args = parse_args()
    # CPU only, also on machines with a CUDA GPU
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    for size in args.imgsz:
        if size % 32:
            raise SystemExit(f"--imgsz must be a multiple of 32, got {size}")

    try:
        import ultralytics  # noqa: F401
    except ImportError:
        raise SystemExit(
            "Ultralytics is not installed. See the instructions at the top of "
            "scripts/export_yolo.py"
        ) from None

    args.output.mkdir(parents=True, exist_ok=True)
    for size in args.imgsz:
        for fmt in args.formats:
            print(f"Exporting {args.model} at {size} to {fmt}")
            print(f"  -> {export_one(args.model, size, fmt, args.output)}")
    print("Done. Copy the output directory to models/yolo/ on the Pi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
