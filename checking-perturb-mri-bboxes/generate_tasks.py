#!/usr/bin/env python3
"""
generate_tasks.py
-----------------
Convert perturbed labels JSON to Label Studio task format.

Reads:
  - results/perturbed_labels.json  (output from perturb_bboxes.py)

Outputs:
  - results/tasks.json  (Label Studio importable JSON)

Usage:
  python generate_tasks.py [options]

  python generate_tasks.py --input-json ./results/perturbed_labels.json
"""

import os
import json
import argparse

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULTS = {
    "input_json":  "./results/perturbed_labels.json",
    "output_json": "./results/tasks.json",
    "image_url_base": "http://localhost:8081",  # serve from results/previews/
}


# ── Conversion ────────────────────────────────────────────────────────────────
def boxes_to_ls_results(boxes: list, original_width: int = 248, original_height: int = 496) -> list:
    """
    Convert perturbed box format to Label Studio result format.

    Input:  [{'label': 'N/A', 'coords': [x1, y1, x2, y2]}, ...]
            coords are normalized 0-1

    Output: [{"from_name": "label", "to_name": "image", "type": "rectanglelabels",
              "value": {"x": 62.0, "y": 45.0, "width": 10.0, "height": 4.0,
                        "rectanglelabels": ["N/A"]}}]
    """
    results = []
    for i, box in enumerate(boxes):
        label = box.get('label')
        if label is None:
            label = 'N/A'  # Default label for Excel-sourced boxes without labels

        coords = box.get('coords', [])
        if len(coords) != 4:
            continue

        x1, y1, x2, y2 = coords

        # Convert from [x1, y1, x2, y2] to LS format [x, y, width, height] in percentages
        x = round(x1 * 100, 2)
        y = round(y1 * 100, 2)
        w = round((x2 - x1) * 100, 2)
        h = round((y2 - y1) * 100, 2)

        results.append({
            "from_name": "label",
            "to_name": "image",
            "type": "rectanglelabels",
            "id": f"box_{i}",
            "value": {
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "rectanglelabels": [label]
            }
        })

    return results


def generate_tasks(perturbed_json: str, image_url_base: str, output_json: str):
    """
    Convert perturbed labels JSON to Label Studio tasks format.

    Each task represents one image with perturbed bounding box annotations.
    """
    # Load perturbed labels
    with open(perturbed_json, 'r') as f:
        perturbed = json.load(f)

    tasks = []
    for entry in perturbed:
        patient_id = entry.get('patient_id', '')
        slide = entry.get('slide', '')
        boxes = entry.get('boxes', [])

        # Preview images are in results/previews/{patient_id}/{slide}.jpg
        preview_path = f"{patient_id}/{slide}.jpg"
        image_url = f"{image_url_base}/{preview_path}" if image_url_base else preview_path

        # Convert boxes to LS result format
        ls_results = boxes_to_ls_results(boxes)

        task = {
            "data": {
                "image": image_url
            },
            "predictions": [{
                "result": ls_results,
                "model_version": "perturbed"
            }]
        }
        tasks.append(task)

    # Write output
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, 'w') as f:
        json.dump(tasks, f, indent=2)

    print(f"Generated {len(tasks)} tasks → {output_json}")
    return tasks


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Convert perturbed labels to Label Studio task format."
    )
    parser.add_argument('--input-json', default=DEFAULTS['input_json'],
                        dest='input_json',
                        help='Input perturbed labels JSON')
    parser.add_argument('--output-json', default=DEFAULTS['output_json'],
                        dest='output_json',
                        help='Output Label Studio tasks JSON')
    parser.add_argument('--image-url-base', default=DEFAULTS['image_url_base'],
                        dest='image_url_base',
                        help='Base URL for image paths (e.g., http://localhost:8081)')
    args = parser.parse_args()

    generate_tasks(args.input_json, args.image_url_base, args.output_json)


if __name__ == '__main__':
    main()