#!/usr/bin/env python3
"""
Convert Label Studio export JSON to x1, y1, x2, y2 normalized format.

Label Studio default export format:
    {"x": 10.5, "y": 20.3, "width": 15.0, "height": 25.0, "rectanglelabels": ["tumor"]}

Output format (normalized x1, y1, x2, y2):
    {"x1": 0.105, "y1": 0.203, "x2": 0.255, "y2": 0.453, "label": "tumor"}
"""

import argparse
import json
import pandas as pd
from pathlib import Path


def convert_bbox_to_x1y1x2y2(bbox: dict) -> dict:
    """
    Convert Label Studio (x, y, width, height) percentage format
    to (x1, y1, x2, y2) normalized format (0-1 range).

    Label Studio uses percentages (0-100), we convert to normalized (0-1).
    """
    x = bbox["x"] / 100.0
    y = bbox["y"] / 100.0
    width = bbox["width"] / 100.0
    height = bbox["height"] / 100.0

    return {
        "x1": round(x, 6),
        "y1": round(y, 6),
        "x2": round(x + width, 6),
        "y2": round(y + height, 6),
    }


def convert_ls_export_to_x1y1x2y2(ls_export: list) -> pd.DataFrame:
    """
    Convert Label Studio export JSON to DataFrame with x1, y1, x2, y2 columns.
    """
    records = []
    for task in ls_export:
        image_name = task.get("image", task.get("data", {}).get("image", "unknown"))

        for annotation in task.get("annotations", []):
            for result in annotation.get("result", []):
                if result.get("type") == "rectanglelabels":
                    value = result.get("value", {})
                    bbox_data = {
                        "x": value.get("x", 0),
                        "y": value.get("y", 0),
                        "width": value.get("width", 0),
                        "height": value.get("height", 0),
                    }
                    coords = convert_bbox_to_x1y1x2y2(bbox_data)
                    labels = value.get("rectanglelabels", [])

                    records.append({
                        "image_name": image_name,
                        "label": labels[0] if labels else "unknown",
                        **coords
                    })

    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser(
        description="Convert Label Studio export to x1,y1,x2,y2 normalized format"
    )
    parser.add_argument("input_json", help="Label Studio export JSON file")
    parser.add_argument("-o", "--output", default="annotations_converted.csv",
                        help="Output CSV file (default: annotations_converted.csv)")
    parser.add_argument("--format", choices=["csv", "json"], default="csv",
                        help="Output format (default: csv)")
    args = parser.parse_args()

    with open(args.input_json, "r") as f:
        ls_export = json.load(f)

    df = convert_ls_export_to_x1y1x2y2(ls_export)

    if df.empty:
        print("No rectangle annotations found in the export.")
        return

    print(f"Converted {len(df)} annotations:")
    print(df.head(10))

    output_path = Path(args.output)
    if args.format == "csv":
        df.to_csv(output_path, index=False)
    else:
        df.to_json(output_path, orient="records", indent=2)

    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()
