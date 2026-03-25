#!/usr/bin/env python3
"""
Convert existing bounding boxes to Label Studio JSON format.

Input format (CSV):
    image_name,x1,y1,x2,y2,label
    brain_001.jpg,0.1,0.2,0.3,0.4,tumor
    brain_001.jpg,0.5,0.6,0.7,0.8,lesion

Output: tasks.json in Label Studio import format
"""

import argparse
import json
import pandas as pd
from pathlib import Path


def convert_bbox_format(x1: float, y1: float, x2: float, y2: float) -> dict:
    """Convert normalized (x1,y1,x2,y2) to Label Studio (x, y, width, height)."""
    return {
        "x": x1 * 100,  # Label Studio uses percentage
        "y": y1 * 100,
        "width": (x2 - x1) * 100,
        "height": (y2 - y1) * 100,
    }


def create_ls_annotation(label: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    """Create a Label Studio annotation object."""
    bbox = convert_bbox_format(x1, y1, x2, y2)
    return {
        "from_name": "label",
        "to_name": "image",
        "type": "rectanglelabels",
        "value": {
            "rectanglelabels": [label],
            **bbox
        }
    }


def main():
    parser = argparse.ArgumentParser(description="Convert bboxes to Label Studio format")
    parser.add_argument("input_csv", help="Input CSV with columns: image_name,x1,y1,x2,y2,label,doctor")
    parser.add_argument("images_dir", help="Directory containing images")
    parser.add_argument("-o", "--output", default="tasks.json", help="Output JSON file")
    args = parser.parse_args()

    df = pd.read_csv(args.input_csv, na_values=[], keep_default_na=False)
    required_cols = ["image_name", "x1", "y1", "x2", "y2", "label", "doctor"]
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f"CSV must contain columns: {required_cols}")

    tasks = []
    for image_name, group in df.groupby("image_name"):
        predictions = [
            create_ls_annotation(row.label, row.x1, row.y1, row.x2, row.y2)
            for _, row in group.iterrows()
        ]
        tasks.append({
            "data": {
                "image": f"/data/local-files/?d=/images/{image_name}"
            },
            "predictions": [{
                "model_version": group["doctor"].iloc[0],
                "result": predictions
            }]
        })

    with open(args.output, "w") as f:
        json.dump(tasks, f, indent=2)

    print(f"Created {len(tasks)} tasks with predictions in {args.output}")


if __name__ == "__main__":
    main()
