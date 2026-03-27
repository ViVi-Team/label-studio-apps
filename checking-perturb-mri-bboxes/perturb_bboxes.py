"""
perturb_bboxes.py
-----------------
Reads gold-standard bounding box annotations from .xlsx and generates
Gaussian-perturbed annotations for all neighboring unlabeled slides.

Cross-MPR model:
  - Distance = |slice_num_A - slice_num_B| only (MPR series adds no penalty)
  - mpr-1_106, mpr-2_106, mpr-3_106 are all at distance 0 from each other

Usage:
  python perturb_bboxes.py [options]

  python perturb_bboxes.py --dry-run
  python perturb_bboxes.py --output-json results/perturbed_labels.json

cd /Volumes/HP_P900/knovel/data-app
python3 perturb_bboxes.py \
  --output-json ./results/perturbed_labels.json \
  --output-csv  ./results/perturbed_labels.csv \
  --render-all \
  --output-images ./results/all_previews

python3 perturb_bboxes.py \
  --output-json ./results/perturbed_labels.json \
  --output-csv  ./results/perturbed_labels.csv \
  --find-keystones \
  --render-keystones \
  --output-keystones ./results/keystone_candidates.csv \
  --output-keystones-images ./results/keystone_previews
"""

import os
import re
import ast
import json
import argparse
import random
import csv
from collections import defaultdict

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULTS = {
    "xlsx":         "./Copy of AI in Dementia Diagnosis Project.xlsx",
    "data_folder":  "./local/Data_by_Patient",
    "output_json":  "./results/perturbed_labels.json",
    "output_csv":   "./results/perturbed_labels.csv",
    "output_images": "./results/previews",
    "max_dist":     10,
    "jitter_base":  0.005,   # σ at distance 0  (outward expansion only)
    "jitter_scale": 0.001,   # extra σ per unit distance
    "seed":         42,
    "num_samples":  0,        # 0 = render all
}


# ── Filename parsing ──────────────────────────────────────────────────────────
_SLICE_RE = re.compile(r'(mpr-\d+)_(\d+)\.jpe?g$', re.IGNORECASE)

def parse_slice_info(filename: str):
    """Return (mpr_series, slice_num) or (None, None)."""
    m = _SLICE_RE.search(filename)
    if m:
        return m.group(1), int(m.group(2))
    return None, None

def parse_patient_slide(filename: str):
    """Return (patient_id, slide_suffix) from 'PID_MRx_mpr-N_NNN.jpg'.
    Example: 'OAS1_0080_MR2_mpr-1_100.jpg' -> ('OAS1_0080', 'MR2_mpr-1_100')"""
    base = re.sub(r'\.jpe?g$', '', filename, flags=re.IGNORECASE)
    m = re.search(r'_(MR\d+)_', base)
    if m:
        parts = base.split(m.group(0), 1)
        return parts[0], f"{m.group(1)}_{parts[1]}"
    return base, ''


# ── Gold standard loader ──────────────────────────────────────────────────────
def parse_bbox(val) -> list:
    """Parse a bbox cell value into a list of [x1,y1,x2,y2] boxes."""
    if pd.isna(val) or str(val).strip() == '':
        return []
    try:
        val_str = str(val).strip()
        if not val_str.startswith('['):
            val_str = f'[{val_str}]'
        parsed = ast.literal_eval(f'[{val_str}]')
        flat = []
        def _dig(lst):
            if (isinstance(lst, list) and len(lst) == 4
                    and all(isinstance(x, (int, float)) for x in lst)):
                flat.append([float(c) for c in lst])
            elif isinstance(lst, list):
                for item in lst:
                    _dig(item)
        _dig(parsed)
        return flat
    except Exception:
        return []


def load_gold_standard(xlsx_path: str) -> dict:
    """Return {filename: {boxes: [{label, coords}], notes}}.

    Multiple rows with the same (Patient ID, Slide) are merged: all their
    bounding boxes are combined into one entry so nothing is lost.
    Boxes from xlsx have no label (label=None).
    """
    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"Excel not found: {xlsx_path}")
    df = pd.read_excel(xlsx_path)
    gold: dict = {}
    total_rows = 0
    for _, row in df.iterrows():
        pid   = str(row.get('Patient ID', '')).strip()
        slide = str(row.get('Slide', '')).strip()
        if not pid or not slide or pid == 'nan' or slide == 'nan':
            continue
        coords_list = parse_bbox(row.get('Corrected and rotated BBOX [x1, y1, x2, y2]'))
        notes = str(row.get('Notes', '')) if pd.notna(row.get('Notes')) else ''
        if not coords_list:
            continue
        total_rows += 1
        # Convert to labelled box format: {label: None, coords: [x1,y1,x2,y2]}
        boxes = [{'label': None, 'coords': coords} for coords in coords_list]
        filename = f"{pid}_MR1_{slide}.jpg"
        if filename in gold:
            # Merge: add boxes, concatenate notes
            gold[filename]['boxes'].extend(boxes)
            if notes and notes not in gold[filename]['notes']:
                gold[filename]['notes'] += (' | ' if gold[filename]['notes'] else '') + notes
        else:
            gold[filename] = {'boxes': boxes, 'notes': notes}
    if total_rows != len(gold):
        print(f"  (xlsx: {total_rows} valid rows → merged into {len(gold)} unique slides "
              f"[{total_rows - len(gold)} duplicate slide rows combined])")
    return gold


def load_curated_from_ls(json_path: str) -> dict:
    """Load curated annotations from Label Studio export JSON.

    Returns {filename: {boxes: [{label: str, coords: [x1,y1,x2,y2]}, ...], notes: str}}.

    Extracts annotations (not predictions) from LS export format.
    Image URL -> filename extraction: http://localhost:8081/OAS1_0001_MR1_mpr-1_106.jpg
    LS bbox format: {x, y, width, height} in percentages (0-100)
    Convert to: [x1, y1, x2, y2] normalized (0-1)
    """
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Label Studio JSON not found: {json_path}")

    with open(json_path, 'r') as f:
        tasks = json.load(f)

    gold: dict = {}
    total_annotations = 0

    for task in tasks:
        image_url = task.get('data', {}).get('image', '')
        # Extract filename from URL: http://localhost:8081/OAS1_0001_MR1_mpr-1_106.jpg
        filename = image_url.split('/')[-1] if '/' in image_url else image_url

        # Get annotations - these are the actual curated results
        annotations = task.get('annotations', [])
        if not annotations:
            continue

        # Use the first completed annotation's results
        # Each result entry has its own label
        boxes = []
        for annotation in annotations:
            results = annotation.get('result', [])
            for result in results:
                value = result.get('value', {})
                rect_labels = value.get('rectanglelabels', [])
                if not rect_labels:
                    continue

                # Get label for THIS specific box (not shared)
                label = rect_labels[0]

                # Convert LS format (x, y, width, height in percentages) to [x1,y1,x2,y2] normalized
                x = value.get('x', 0) / 100.0
                y = value.get('y', 0) / 100.0
                w = value.get('width', 0) / 100.0
                h = value.get('height', 0) / 100.0

                coords = [round(x, 6), round(y, 6), round(x + w, 6), round(y + h, 6)]
                boxes.append({'label': label, 'coords': coords})
                total_annotations += 1

        if boxes:
            gold[filename] = {'boxes': boxes, 'notes': ''}

    print(f"  (LS curated: {len(gold)} slides, {total_annotations} annotated boxes)")
    return gold



# ── Local inventory ───────────────────────────────────────────────────────────
def build_patient_inventory(data_folder: str):
    """
    Scan Data_by_Patient/{status}/{patient_id}/*.jpg
    Returns:
      inventory:  {patient_id: [filename, ...]} sorted
      path_map:   {filename: full_abs_path}
      relpath_map: {filename: relative_path_from_data_folder} e.g. "Non-Dementia/OAS1_0001/file.jpg"
    """
    inventory = defaultdict(list)
    path_map = {}
    relpath_map = {}
    if not os.path.exists(data_folder):
        raise FileNotFoundError(f"Data folder not found: {data_folder}")
    for status_dir in sorted(os.listdir(data_folder)):
        sp = os.path.join(data_folder, status_dir)
        if not os.path.isdir(sp) or status_dir.startswith('.'):
            continue
        for patient_dir in sorted(os.listdir(sp)):
            pp = os.path.join(sp, patient_dir)
            if not os.path.isdir(pp) or patient_dir.startswith('.'):
                continue
            for f in os.listdir(pp):
                if f.lower().endswith(('.jpg', '.jpeg', '.png')) and not f.startswith('.'):
                    inventory[patient_dir].append(f)
                    path_map[f] = os.path.join(pp, f)
                    relpath_map[f] = os.path.join(status_dir, patient_dir, f)
    for pid in inventory:
        inventory[pid] = sorted(inventory[pid])
    return dict(inventory), path_map, relpath_map


# ── Perturbation ──────────────────────────────────────────────────────────────
def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))

def perturb_labelled_box(labelled_box: dict, sigma: float, rng: random.Random) -> dict:
    """
    Expand box outward by a small positive amount, preserving the label.
    abs(gauss) ensures we ONLY expand, never shrink — safer for medical annotations.
      x1 moves left  (subtract),  y1 moves up   (subtract)
      x2 moves right (add),       y2 moves down  (add)

    Args:
        labelled_box: {'label': str, 'coords': [x1,y1,x2,y2]}

    Returns:
        {'label': str, 'coords': [x1,y1,x2,y2]} with perturbed coords
    """
    x1, y1, x2, y2 = labelled_box['coords']
    x1 = _clamp(x1 - abs(rng.gauss(0, sigma)))
    y1 = _clamp(y1 - abs(rng.gauss(0, sigma)))
    x2 = _clamp(x2 + abs(rng.gauss(0, sigma)))
    y2 = _clamp(y2 + abs(rng.gauss(0, sigma)))
    # x1<x2 and y1<y2 are guaranteed by expansion direction + clamping
    return {
        'label': labelled_box['label'],
        'coords': [round(x1, 6), round(y1, 6), round(x2, 6), round(y2, 6)]
    }


# ── Main logic ────────────────────────────────────────────────────────────────
def generate_perturbed_labels(
    gold: dict,
    inventory: dict,
    relpath_map: dict,
    max_dist: int,
    jitter_base: float,
    jitter_scale: float,
    seed: int,
) -> list:
    """
    For every slide in the inventory that does NOT have a gold label,
    find the nearest gold slide (by |slice_num| distance, ignoring MPR),
    and return a perturbed annotation entry.

    Box format throughout: [{label: str|None, coords: [x1,y1,x2,y2]}, ...]

    Returns list of dicts:
      {patient_id, slide, origin_slice, boxes: [{label, coords}], notes, distance}
    """
    rng = random.Random(seed)
    results = []

    # Build per-(patient, mpr_series) slice bounds: {pid: {mpr: (min_slice, max_slice)}}
    mpr_bounds: dict = defaultdict(lambda: defaultdict(lambda: [10**9, -10**9]))
    for pid, files in inventory.items():
        for fname in files:
            mpr, snum = parse_slice_info(fname)
            if snum is None:
                continue
            b = mpr_bounds[pid][mpr]
            if snum < b[0]: b[0] = snum
            if snum > b[1]: b[1] = snum

    # Build gold lookup: patient_id -> {slice_num -> (filename, boxes: [{label, coords}], notes)}
    gold_by_patient = defaultdict(dict)
    for fname, data in gold.items():
        pid, _ = parse_patient_slide(fname)
        _, snum = parse_slice_info(fname)
        if snum is not None:
            # Multiple gold entries at same slice_num? Keep all
            gold_by_patient[pid].setdefault(snum, []).append((fname, data['boxes'], data['notes']))

    total_skipped_gold = 0
    total_skipped_no_neighbor = 0

    for pid, files in sorted(inventory.items()):
        gold_slices = gold_by_patient.get(pid, {})
        if not gold_slices:
            # No gold for this patient at all — skip
            continue

        sorted_gold_nums = sorted(gold_slices.keys())

        for fname in files:
            # Skip if this IS a gold slide
            if fname in gold:
                total_skipped_gold += 1
                continue

            target_mpr, snum = parse_slice_info(fname)
            if snum is None:
                continue

            # Explicit MPR bounds: clamp search to [min_slice, max_slice] for this MPR
            mpr_min, mpr_max = mpr_bounds[pid].get(target_mpr, [0, 10**9])

            # Find closest gold slice_num — but only consider gold slices whose
            # propagation window stays within this MPR's valid bounds.
            # i.e. gold at g_snum can reach this target only if:
            #   |snum - g_snum| <= max_dist  AND  snum in [mpr_min, mpr_max]
            best_dist = max_dist + 1
            best_gold_fname = None
            best_boxes = None
            best_notes = ''

            if not (mpr_min <= snum <= mpr_max):
                # Target slice is outside this MPR's valid range — should never happen
                # if inventory is clean, but guard explicitly.
                total_skipped_no_neighbor += 1
                continue

            for g_snum in sorted_gold_nums:
                d = abs(snum - g_snum)
                if d < best_dist:
                    best_dist = d
                    # Pick the first gold entry at this slice_num
                    gf, gb, gn = gold_slices[g_snum][0]
                    best_gold_fname = gf
                    best_boxes = gb
                    best_notes = gn

            if best_dist > max_dist:
                total_skipped_no_neighbor += 1
                continue

            # Perturb each labelled box
            sigma = jitter_base + jitter_scale * best_dist
            perturbed_boxes = [perturb_labelled_box(b, sigma, rng) for b in best_boxes]

            patient_id, slide_suffix = parse_patient_slide(fname)
            _, origin_slide = parse_patient_slide(best_gold_fname)

            results.append({
                'patient_id':   patient_id,
                'slide':        slide_suffix,
                'origin_slice': origin_slide,
                'origin_fname': best_gold_fname,
                'boxes':        perturbed_boxes,  # [{label, coords}, ...]
                'notes':        best_notes,
                'distance':     best_dist,
                'image_path':   relpath_map.get(fname, ''),  # relative path for URL
            })

    print(f"\n── Summary ─────────────────────────────────────")
    print(f"  Gold slides (skipped):          {total_skipped_gold}")
    print(f"  Slides >max_dist (skipped):     {total_skipped_no_neighbor}")
    print(f"  Perturbed entries generated:    {len(results)}")
    print(f"────────────────────────────────────────────────\n")
    return results


# ── Image rendering ──────────────────────────────────────────────────────────
def _load_image(img_path: str) -> Image.Image:
    """Load image, rotate to portrait if landscape."""
    img = Image.open(img_path).convert('RGB')
    if img.width > img.height:
        img = img.transpose(Image.ROTATE_90)
    return img


# Label-specific bounding box colors (matching Label Studio config.xml)
LABEL_COLORS = {
    'N/A':    '#FF6B6B',
    'GCA':    '#4ECDC4',
    'Koedam': '#FFE66D',
    'MTA':    '#0061ff',
}


def _get_box_color(box) -> str:
    """Get color for a box based on its label."""
    if isinstance(box, dict):
        label = box.get('label')
    else:
        label = None
    return LABEL_COLORS.get(label, 'red')  # default to red


def _draw_boxes(img: Image.Image, boxes: list, color: str, width: int = 2) -> Image.Image:
    """Draw bounding boxes (relative coords) on a copy of img.

    Args:
        boxes: list of either [x1,y1,x2,y2] (legacy) or {'label': str, 'coords': [x1,y1,x2,y2]}
        color: fallback color if box has no label (for legacy support)
    """
    out = img.copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    for box in boxes:
        if isinstance(box, dict):
            coords = box.get('coords', [])
            if len(coords) != 4:
                continue
            x1, y1, x2, y2 = coords
            box_color = _get_box_color(box)
        else:
            # Legacy format: plain list
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = box
            box_color = color
        draw.rectangle(
            [x1 * w, y1 * h, x2 * w, y2 * h],
            outline=box_color, width=width
        )
    return out


def _make_label_bar(width: int, text: str, bg: str, fg: str = 'white') -> Image.Image:
    """Thin coloured label bar with text."""
    bar = Image.new('RGB', (width, 28), bg)
    draw = ImageDraw.Draw(bar)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 16)
    except Exception:
        font = ImageFont.load_default()
    draw.text((6, 4), text, fill=fg, font=font)
    return bar


def render_images(
    results: list,
    gold: dict,
    path_map: dict,
    output_dir: str,
    num_samples: int = 0,
):
    """
    For each perturbed entry, render a side-by-side image:
      Left : image with GOLD box (red)
      Right: same image with PERTURBED box (yellow)
    Saved to output_dir/{patient_id}/{slide}.jpg

    Args:
        num_samples: if > 0, render only this many entries (evenly sampled).
    """
    os.makedirs(output_dir, exist_ok=True)

    entries = results
    if num_samples > 0 and num_samples < len(results):
        step = len(results) // num_samples
        entries = results[::step][:num_samples]

    rendered = 0
    skipped  = 0
    for entry in entries:
        pid          = entry['patient_id']
        slide        = entry['slide']
        origin_slide = entry['origin_slice']
        perturbed_boxes = entry['boxes']
        distance     = entry['distance']

        # Reconstruct filenames
        target_fname = f"{pid}_{slide}.jpg"
        origin_fname = f"{pid}_{origin_slide}.jpg"

        target_path = path_map.get(target_fname)
        origin_path = path_map.get(origin_fname)

        if not target_path or not os.path.exists(target_path):
            skipped += 1
            continue

        try:
            # Target image with perturbed box
            target_img = _load_image(target_path)
            perturbed_img = _draw_boxes(target_img, perturbed_boxes, color='yellow', width=2)

            # Gold image — use origin slide if available, else same image
            gold_boxes = gold.get(origin_fname, {}).get('boxes', [])
            if origin_path and os.path.exists(origin_path) and origin_fname != target_fname:
                gold_img = _load_image(origin_path)
            else:
                gold_img = target_img.copy()
            gold_img = _draw_boxes(gold_img, gold_boxes, color='red', width=2)

            # Resize to same height for side-by-side
            tgt_h = target_img.height
            gld_w = int(gold_img.width * tgt_h / gold_img.height)
            gold_img = gold_img.resize((gld_w, tgt_h), Image.LANCZOS)
            perturbed_img = perturbed_img.resize((target_img.width, tgt_h), Image.LANCZOS)

            # Label bars
            gold_label      = _make_label_bar(gld_w, f'GOLD  ({origin_slide})', '#c0392b')
            perturbed_label = _make_label_bar(perturbed_img.width,
                                              f'PERTURBED  dist={distance}  ({slide})', '#b8860b')

            # Stack label + image for each side
            left  = Image.new('RGB', (gld_w, tgt_h + 28))
            left.paste(gold_label, (0, 0))
            left.paste(gold_img,   (0, 28))

            right = Image.new('RGB', (perturbed_img.width, tgt_h + 28))
            right.paste(perturbed_label, (0, 0))
            right.paste(perturbed_img,   (0, 28))

            # 4px separator
            sep = Image.new('RGB', (4, tgt_h + 28), (50, 50, 50))
            composite_w = left.width + 4 + right.width
            composite = Image.new('RGB', (composite_w, tgt_h + 28))
            composite.paste(left,  (0, 0))
            composite.paste(sep,   (left.width, 0))
            composite.paste(right, (left.width + 4, 0))

            # Save
            patient_dir = os.path.join(output_dir, pid)
            os.makedirs(patient_dir, exist_ok=True)
            out_name = f"{slide}.jpg"
            composite.save(os.path.join(patient_dir, out_name), 'JPEG', quality=88)
            rendered += 1

        except Exception as e:
            print(f"  ⚠ Render failed for {target_fname}: {e}")
            skipped += 1

    print(f"Images rendered → {output_dir}  ({rendered} saved, {skipped} skipped)")


def render_all_images(
    results: list,
    gold: dict,
    inventory: dict,
    path_map: dict,
    output_dir: str,
    num_samples: int = 0,
):
    """
    For EVERY slide of every patient that has gold coverage, render a 3-panel image:
      Panel 1 (left)  : Original image — no boxes
      Panel 2 (center): Image with GOLD/source boxes drawn (red)
      Panel 3 (right) : Image with PERTURBED boxes (yellow), or status label

    Status label colours:
      #c0392b (red)    — GOLD STANDARD slide (exact gold annotation)
      #b8860b (gold)   — PERTURBED   (within max_dist of a gold slide)
      #555555 (grey)   — OUT OF RANGE (too far from any gold slide)
    """
    os.makedirs(output_dir, exist_ok=True)

    # Build fast lookups
    perturbed_lookup = {(r['patient_id'], r['slide']): r for r in results}
    # patients that have ANY gold coverage
    gold_patients = set()
    for fname in gold:
        pid, _ = parse_patient_slide(fname)
        gold_patients.add(pid)

    # Build list of all slides to render
    all_entries = []
    for pid in sorted(gold_patients):
        for fname in inventory.get(pid, []):
            all_entries.append((pid, fname))

    if num_samples > 0 and num_samples < len(all_entries):
        step = len(all_entries) // num_samples
        all_entries = all_entries[::step][:num_samples]

    rendered = skipped = 0
    cnt = {'gold': 0, 'perturbed': 0, 'out_of_range': 0}
    total = len(all_entries)

    for i, (pid, fname) in enumerate(all_entries):
        if i % 500 == 0:
            print(f"  {i}/{total} rendered...", flush=True)

        target_path = path_map.get(fname)
        if not target_path or not os.path.exists(target_path):
            skipped += 1
            continue

        _, slide_suffix = parse_patient_slide(fname)
        perturb_key = (pid, slide_suffix)
        target_fname_key = fname  # e.g. OAS1_0001_MR1_mpr-1_106.jpg

        # Determine status
        if target_fname_key in gold:
            status     = 'gold'
            gold_boxes = gold[target_fname_key]['boxes']
            pert_boxes = None
            origin_slide = slide_suffix
        elif perturb_key in perturbed_lookup:
            entry        = perturbed_lookup[perturb_key]
            status       = 'perturbed'
            origin_slide = entry['origin_slice']
            origin_fname = entry.get('origin_fname', '')
            if not origin_fname:
                # fallback for old cache if any
                origin_fname = f"{pid}_{origin_slide}.jpg"
            gold_boxes   = gold.get(origin_fname, {}).get('boxes', [])
            pert_boxes   = entry['boxes']
        else:
            status       = 'out_of_range'
            gold_boxes   = []
            pert_boxes   = None
            origin_slide = ''

        try:
            target_img = _load_image(target_path)
            W, H = target_img.size
            gray_img = Image.new('RGB', (W, H), (80, 80, 80))  # gray placeholder

            # Panel 1: Original (always the target image, no boxes)
            orig_panel = target_img.copy()

            # Panel 2: Gold SOURCE image with gold boxes drawn on it
            if status == 'gold':
                # The gold source IS this image itself
                gold_src_img = target_img.copy()
            elif status == 'perturbed':
                # Load the actual origin slide image
                # (origin_fname already determined above)
                origin_path  = path_map.get(origin_fname)
                if origin_path and os.path.exists(origin_path):
                    gold_src_img = _load_image(origin_path)
                    gold_src_img = gold_src_img.resize((W, H), Image.LANCZOS)
                else:
                    gold_src_img = gray_img.copy()
            else:  # out_of_range
                gold_src_img = gray_img.copy()

            gold_panel = _draw_boxes(gold_src_img, gold_boxes, color='red', width=2)

            # Panel 3: Perturbed boxes on target, or gold (green) if this IS gold, or gray
            if status == 'perturbed':
                pert_panel = _draw_boxes(target_img.copy(), pert_boxes, color='yellow', width=2)
            elif status == 'gold':
                pert_panel = _draw_boxes(target_img.copy(), gold_boxes, color='lime', width=2)
            else:  # out_of_range — gray
                pert_panel = gray_img.copy()

            # Label bars
            if status == 'gold':
                p3_bg = '#27ae60'  # green
                p3_text = f'GOLD STANDARD ({slide_suffix})'
                p2_text = f'GOLD BOXES ({slide_suffix})'
            elif status == 'perturbed':
                dist = perturbed_lookup[perturb_key]['distance']
                p3_bg = '#b8860b'
                p3_text = f'PERTURBED  dist={dist}  ({slide_suffix})'
                p2_text = f'GOLD SOURCE ({origin_slide})'
            else:
                p3_bg = '#444444'
                p3_text = f'OUT OF RANGE ({slide_suffix})'
                p2_text = f'NEAREST GOLD: none'

            bar1 = _make_label_bar(W, f'ORIGINAL ({slide_suffix})', '#1a252f')
            bar2 = _make_label_bar(W, p2_text,                      '#8e1a1a')
            bar3 = _make_label_bar(W, p3_text,                       p3_bg)

            def _stack(panel, bar):
                out = Image.new('RGB', (W, H + 28))
                out.paste(bar,   (0, 0))
                out.paste(panel, (0, 28))
                return out

            p1 = _stack(orig_panel, bar1)
            p2 = _stack(gold_panel, bar2)
            p3 = _stack(pert_panel, bar3)

            sep = Image.new('RGB', (4, H + 28), (60, 60, 60))
            composite = Image.new('RGB', (W * 3 + 8, H + 28))
            composite.paste(p1,  (0, 0))
            composite.paste(sep, (W, 0))
            composite.paste(p2,  (W + 4, 0))
            composite.paste(sep, (W * 2 + 4, 0))
            composite.paste(p3,  (W * 2 + 8, 0))

            patient_dir = os.path.join(output_dir, pid)
            os.makedirs(patient_dir, exist_ok=True)
            composite.save(os.path.join(patient_dir, f"{slide_suffix}.jpg"), 'JPEG', quality=85)
            cnt[status] += 1
            rendered += 1

        except Exception as e:
            print(f"  ⚠ Failed {fname}: {e}")
            skipped += 1

    print(f"All-images rendered → {output_dir}  ({rendered} saved, {skipped} skipped)")
    print(f"  🟢 GOLD STANDARD : {cnt['gold']}")
    print(f"  🟡 PERTURBED     : {cnt['perturbed']}")
    print(f"  ◻  OUT OF RANGE  : {cnt['out_of_range']}")


# ── Output ────────────────────────────────────────────────────────────────────
def write_json(results: list, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"JSON saved → {path}")


def write_csv(results: list, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['patient_id', 'slide', 'origin_slice', 'distance',
                         'box_index', 'x1', 'y1', 'x2', 'y2', 'label', 'notes'])
        for entry in results:
            for i, box in enumerate(entry['boxes']):
                if isinstance(box, dict):
                    label = box.get('label', '')
                    coords = box.get('coords', [])
                else:
                    # Legacy format
                    label = ''
                    coords = box
                writer.writerow([
                    entry['patient_id'],
                    entry['slide'],
                    entry['origin_slice'],
                    entry['distance'],
                    i,
                    *coords,
                    label,
                    entry['notes'],
                ])
    print(f"CSV saved → {path}")


# ── Keystone candidate finder ─────────────────────────────────────────────────
def find_keystone_candidates(
    gold: dict,
    inventory: dict,
    max_dist: int,
) -> list:
    """
    Greedy set-cover simulation: pick one keystone, simulate it as annotated
    (add to virtual_gold), re-compute uncovered slides, repeat.

    This correctly handles cascading coverage: annotating slide X
    may make a previously-separate desert merge with a covered region,
    eliminating the need for a second keystone entirely.

    Coverage is slice-number-agnostic across MPRs (mpr-1 slice 130 ==
    mpr-2 slice 130 by definition), so virtual_gold is a plain set of ints.

    Returns ordered list (rank 1 = highest priority) with:
      {patient_id, slide, mpr_series, slice_num,
       dist_to_nearest_gold, region_size, potential_gain, cumulative_covered}
    """
    from collections import defaultdict as _dd

    # Real gold slice numbers per patient (cross-MPR)
    gold_slices_by_patient = _dd(set)
    for fname in gold:
        pid, _ = parse_patient_slide(fname)
        _, snum = parse_slice_info(fname)
        if snum is not None:
            gold_slices_by_patient[pid].add(snum)

    # Per-patient: sorted slice list, representative fname per slice
    inv_by_patient = _dd(dict)   # pid -> {snum: fname}
    for pid, files in inventory.items():
        for fname in files:
            _, snum = parse_slice_info(fname)
            if snum is not None and snum not in inv_by_patient[pid]:
                inv_by_patient[pid][snum] = fname

    candidates = []
    cumulative = 0

    for pid in sorted(inv_by_patient.keys()):
        gold_nums = gold_slices_by_patient.get(pid, set())
        if not gold_nums:
            continue

        all_snums     = sorted(inv_by_patient[pid].keys())
        gold_slice_set = set(gold_nums)   # real gold — never covered by keystones
        virtual_gold  = set(gold_nums)    # grows as we simulate annotations

        while True:
            # Uncovered = not real gold AND too far from any virtual gold
            uncovered = [s for s in all_snums
                         if s not in gold_slice_set
                         and min(abs(s - g) for g in virtual_gold) > max_dist]
            if not uncovered:
                break

            # Group uncovered into contiguous desert regions
            regions = []
            cur = [uncovered[0]]
            for s in uncovered[1:]:
                if s - cur[-1] <= 1:
                    cur.append(s)
                else:
                    regions.append(cur)
                    cur = [s]
            regions.append(cur)

            # Score midpoint of each region: how many TOTAL uncovered slices
            # would it cover (cross-region cascading included because we score
            # against all uncovered, not just the current region)
            best_snum = best_gain = best_region_size = -1
            for region in regions:
                mid  = region[len(region) // 2]
                gain = sum(1 for s in uncovered if abs(s - mid) <= max_dist)
                if gain > best_gain:
                    best_gain, best_snum, best_region_size = gain, mid, len(region)

            if best_snum < 0:
                break

            rep_fname = inv_by_patient[pid].get(best_snum, '')
            mpr, _    = parse_slice_info(rep_fname)
            _, slide  = parse_patient_slide(rep_fname)
            dist_nearest = min(abs(best_snum - g) for g in virtual_gold)

            # Simulate the annotation
            virtual_gold.add(best_snum)
            cumulative += best_gain

            candidates.append({
                'patient_id':           pid,
                'slide':                slide,
                'mpr_series':           mpr or 'mpr-1',
                'slice_num':            best_snum,
                'dist_to_nearest_gold': dist_nearest,
                'region_size':          best_region_size,
                'potential_gain':       best_gain,
                'cumulative_covered':   cumulative,
            })

    # Final sort: highest coverage gain first
    candidates.sort(key=lambda x: (-x['potential_gain'], x['dist_to_nearest_gold']))
    return candidates

def write_keystones_csv(candidates: list, path: str):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['rank', 'patient_id', 'slide', 'mpr_series',
                         'slice_num', 'dist_to_nearest_gold',
                         'region_size', 'potential_gain', 'cumulative_covered'])
        for i, c in enumerate(candidates, 1):
            writer.writerow([i, c['patient_id'], c['slide'], c['mpr_series'],
                             c['slice_num'], c['dist_to_nearest_gold'],
                             c['region_size'], c['potential_gain'],
                             c.get('cumulative_covered', '')])
    print(f"Keystone candidates saved → {path}  ({len(candidates)} rows)")



def render_keystone_previews(
    candidates: list,
    gold: dict,
    path_map: dict,
    output_dir: str,
    num_samples: int = 0,
):
    """
    For each keystone candidate, render a single-panel image with a CYAN
    crosshair marker and the nearest gold boxes overlaid (red), so the
    doctor can see what region needs coverage.
    """
    os.makedirs(output_dir, exist_ok=True)

    entries = candidates
    if num_samples > 0 and num_samples < len(candidates):
        entries = candidates[:num_samples]

    rendered = skipped = 0
    for rank, c in enumerate(entries, 1):
        pid        = c['patient_id']
        slide      = c['slide']
        fname      = f"{pid}_{slide}.jpg"
        tgt_path   = path_map.get(fname)

        if not tgt_path or not os.path.exists(tgt_path):
            skipped += 1
            continue

        try:
            img = _load_image(tgt_path)
            W, H = img.size
            draw = ImageDraw.Draw(img)
            # Cyan crosshair in center
            cx, cy = W // 2, H // 2
            draw.line([(cx - 20, cy), (cx + 20, cy)], fill='cyan', width=2)
            draw.line([(cx, cy - 20), (cx, cy + 20)], fill='cyan', width=2)
            draw.ellipse([(cx-8, cy-8), (cx+8, cy+8)], outline='cyan', width=2)

            label_text = (f"KEYSTONE #{rank}  |  {pid}  |  {slide}  |  "
                          f"gain={c['potential_gain']}  region={c['region_size']}")
            bar = _make_label_bar(W, label_text, '#006994')  # ocean blue

            out = Image.new('RGB', (W, H + 28))
            out.paste(bar, (0, 0))
            out.paste(img, (0, 28))

            patient_dir = os.path.join(output_dir, pid)
            os.makedirs(patient_dir, exist_ok=True)
            out.save(os.path.join(patient_dir, f"rank{rank:04d}_{slide}.jpg"),
                     'JPEG', quality=88)
            rendered += 1
        except Exception as e:
            print(f"  ⚠ Keystone render failed {fname}: {e}")
            skipped += 1

    print(f"Keystone previews → {output_dir}  ({rendered} saved, {skipped} skipped)")


# ── Dry-run report ────────────────────────────────────────────────────────────
def print_dry_run_report(gold: dict, inventory: dict, results: list, max_dist: int):
    from collections import Counter
    patients = sorted(inventory.keys())
    gold_counts = Counter()
    for fname in gold:
        pid, _ = parse_patient_slide(fname)
        gold_counts[pid] += 1
    perturbed_counts = Counter(r['patient_id'] for r in results)

    print(f"\n{'Patient':<20} {'Gold':>6} {'Perturbed':>10} {'Total files':>12}")
    print("─" * 52)
    for pid in patients:
        g = gold_counts.get(pid, 0)
        p = perturbed_counts.get(pid, 0)
        t = len(inventory.get(pid, []))
        if g > 0:
            print(f"{pid:<20} {g:>6} {p:>10} {t:>12}")
    print("─" * 52)
    total_g = sum(gold_counts.values())
    total_p = len(results)
    print(f"{'TOTAL':<20} {total_g:>6} {total_p:>10}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Generate perturbed bounding boxes from gold standard XLSX."
    )
    parser.add_argument('--xlsx',          default=DEFAULTS['xlsx'])
    parser.add_argument('--curated-json',  default=None,
                        dest='curated_json',
                        help='Path to Label Studio export JSON with curated annotations')
    parser.add_argument('--data-folder',   default=DEFAULTS['data_folder'],
                        dest='data_folder',
                        help='Path to Data_by_Patient/ directory')
    parser.add_argument('--max-dist',      type=int,   default=DEFAULTS['max_dist'],
                        dest='max_dist',
                        help='Max |slice_A - slice_B| distance')
    parser.add_argument('--jitter-base',   type=float, default=DEFAULTS['jitter_base'],
                        dest='jitter_base',
                        help='Gaussian sigma at distance 0')
    parser.add_argument('--jitter-scale',  type=float, default=DEFAULTS['jitter_scale'],
                        dest='jitter_scale',
                        help='Extra sigma per unit distance')
    parser.add_argument('--seed',          type=int,   default=DEFAULTS['seed'])
    parser.add_argument('--output-json',   default=DEFAULTS['output_json'],
                        dest='output_json')
    parser.add_argument('--output-csv',    default=DEFAULTS['output_csv'],
                        dest='output_csv')
    parser.add_argument('--render-images', action='store_true', dest='render_images',
                        help='Render side-by-side gold vs perturbed preview images')
    parser.add_argument('--render-all',    action='store_true', dest='render_all',
                        help='Render 3-panel (original|gold|perturbed) for ALL slides')
    parser.add_argument('--output-images', default=DEFAULTS['output_images'],
                        dest='output_images',
                        help='Folder to save rendered preview images')
    parser.add_argument('--num-samples',   type=int, default=DEFAULTS['num_samples'],
                        dest='num_samples',
                        help='Number of images to render (0 = all)')
    parser.add_argument('--dry-run',       action='store_true', dest='dry_run',
                        help='Print stats only, write no files')
    parser.add_argument('--find-keystones', action='store_true', dest='find_keystones',
                        help='Find best slides for doctors to annotate next (coverage maximising)')
    parser.add_argument('--output-keystones', default='./results/keystone_candidates.csv',
                        dest='output_keystones',
                        help='CSV path for keystone candidates')
    parser.add_argument('--render-keystones', action='store_true', dest='render_keystones',
                        help='Render preview images for keystone candidates')
    parser.add_argument('--output-keystones-images',
                        default='./results/keystone_previews',
                        dest='output_keystones_images',
                        help='Folder for keystone preview images')
    args = parser.parse_args()

    # Load gold standard from either curated JSON or xlsx
    if args.curated_json:
        print(f"Loading curated annotations from: {args.curated_json}")
        gold = load_curated_from_ls(args.curated_json)
    else:
        print(f"Loading gold standard from: {args.xlsx}")
        gold = load_gold_standard(args.xlsx)
    print(f"  Gold-labeled slides: {len(gold)}")

    print(f"Scanning inventory from: {args.data_folder}")
    inventory, path_map, relpath_map = build_patient_inventory(args.data_folder)
    total_files = sum(len(v) for v in inventory.values())
    print(f"  Patients: {len(inventory)}   Total image files: {total_files}")

    print(f"\nGenerating perturbed labels (max_dist={args.max_dist}, "
          f"σ_base={args.jitter_base}, σ_scale={args.jitter_scale}) ...")
    results = generate_perturbed_labels(
        gold, inventory, relpath_map,
        max_dist=args.max_dist,
        jitter_base=args.jitter_base,
        jitter_scale=args.jitter_scale,
        seed=args.seed,
    )

    if args.dry_run:
        print_dry_run_report(gold, inventory, results, args.max_dist)
        print("(dry-run) No files written.")
        return

    print_dry_run_report(gold, inventory, results, args.max_dist)
    write_json(results, args.output_json)
    write_csv(results, args.output_csv)

    if args.render_images:
        n = args.num_samples
        label = f'{n} samples' if n > 0 else 'all'
        print(f"\nRendering 2-panel preview images ({label}) → {args.output_images}")
        render_images(
            results, gold, path_map,
            output_dir=args.output_images,
            num_samples=n,
        )

    if args.render_all:
        n = args.num_samples
        label = f'{n} samples' if n > 0 else 'all'
        print(f"\nRendering 3-panel all-images ({label}) → {args.output_images}")
        render_all_images(
            results, gold, inventory, path_map,
            output_dir=args.output_images,
            num_samples=n,
        )

    if args.find_keystones:
        print("\nFinding keystone annotation candidates...")
        keystones = find_keystone_candidates(gold, inventory, max_dist=args.max_dist)
        total_gain = sum(k['potential_gain'] for k in keystones)
        print(f"  {len(keystones)} keystone candidates found")
        print(f"  Total potential gain: {total_gain} slides would become perturbed")
        write_keystones_csv(keystones, args.output_keystones)
        if args.render_keystones:
            render_keystone_previews(
                keystones, gold, path_map,
                output_dir=args.output_keystones_images,
                num_samples=args.num_samples,
            )

    print("\n✓ Done.")


if __name__ == '__main__':
    main()
