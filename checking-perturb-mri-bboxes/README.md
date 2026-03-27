# Perturb MRI Bounding Boxes

Generates Gaussian-perturbed bounding box annotations for neighboring unlabeled slides based on gold-standard annotations.

## Project Structure

```
checking-perturb-mri-bboxes/
├── perturb_bboxes.py      # Main perturbation script
├── generate_tasks.py      # Convert perturbed labels to Label Studio tasks
├── server.py              # CORS-enabled image server for Label Studio
├── Copy of AI in Dementia Diagnosis Project.xlsx  # Gold standard source
├── local/
│   └── Data_by_Patient/   # Patient brain images
└── results/               # Output directory
```

## Quick Start

### Option 1: From Excel Gold Standard

```bash
cd /Volumes/HP_P900/knovel/label-studio-apps/checking-perturb-mri-bboxes

python3 perturb_bboxes.py \
  --xlsx "./Copy of AI in Dementia Diagnosis Project.xlsx" \
  --data-folder "./local/Data_by_Patient" \
  --output-json "./results/perturbed_labels.json" \
  --output-csv  "./results/perturbed_labels.csv"
```

### Option 2: From Curated Label Studio Annotations

```bash
python3 perturb_bboxes.py \
  --curated-json "../project-21-at-2026-03-27-14-39-5672e4c2.json" \
  --data-folder "./local/Data_by_Patient" \
  --output-json "./results/perturbed_labels.json" \
  --output-csv  "./results/perturbed_labels.csv"
```

## Workflow: Curated Annotations → Perturbed Labels

1. **Annotate images in Label Studio**
   - Use `curating-mri-bboxes/` project
   - Export annotations as JSON

2. **Run perturbation with curated JSON**
   ```bash
   python3 perturb_bboxes.py \
     --curated-json "/path/to/curated-export.json" \
     --data-folder "./local/Data_by_Patient" \
     --output-json "./results/perturbed_labels.json" \
     --output-csv  "./results/perturbed_labels.csv"
   ```

3. **Generate Label Studio tasks from perturbed labels**
   ```bash
   python3 generate_tasks.py \
     --input-json "./results/perturbed_labels.json" \
     --output-json "./results/tasks.json" \
     --image-url-base "http://localhost:8081"
   ```

4. **Import perturbed tasks back to Label Studio**
   - In Label Studio: Create new project or use existing
   - Import `results/tasks.json` as pre-annotated tasks

## Command-Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--xlsx` | Path to gold standard Excel file | `./Copy of AI in Dementia Diagnosis Project.xlsx` |
| `--curated-json` | Path to Label Studio export JSON | `None` |
| `--data-folder` | Path to patient image directory | `./local/Data_by_Patient` |
| `--max-dist` | Max slice distance for perturbation | `10` |
| `--jitter-base` | Gaussian σ at distance 0 | `0.005` |
| `--jitter-scale` | Extra σ per unit distance | `0.001` |
| `--seed` | Random seed | `42` |
| `--output-json` | Output JSON path | `./results/perturbed_labels.json` |
| `--output-csv` | Output CSV path | `./results/perturbed_labels.csv` |
| `--dry-run` | Print stats without writing files | `False` |
| `--render-all` | Render 3-panel preview images | `False` |
| `--output-images` | Preview images output dir | `./results/previews` |
| `--num-samples` | Number of images to render (0=all) | `0` |
| `--find-keystones` | Find optimal annotation candidates | `False` |
| `--render-keystones` | Render keystone candidate previews | `False` |

## Generate Tasks Script (`generate_tasks.py`)

| Argument | Description | Default |
|----------|-------------|---------|
| `--input-json` | Input perturbed labels JSON | `./results/perturbed_labels.json` |
| `--output-json` | Output Label Studio tasks JSON | `./results/tasks.json` |
| `--image-url-base` | Base URL for images | `http://localhost:8081` |

## Box Data Structure

Each box now includes a **per-box label**:

```python
{'label': 'N/A', 'coords': [x1, y1, x2, y2]}
```

| Field | Type | Description |
|-------|------|-------------|
| `label` | str or None | Box label (N/A, GCA, Koedam, MTA) or None for Excel source |
| `coords` | list[float] | Bounding box [x1, y1, x2, y2] normalized 0-1 |

## CSV Output Format

```
patient_id, slide, origin_slice, distance, box_index, x1, y1, x2, y2, label, notes
OAS1_0001, mpr-1_106, mpr-1_106, 0, 0, 0.62, 0.45, 0.72, 0.49, N/A, ...
OAS1_0001, mpr-1_106, mpr-1_106, 0, 1, 0.31, 0.45, 0.40, 0.50, GCA, ...
```

## Cross-MPR Model

- Distance = |slice_num_A - slice_num_B| only (MPR series adds no penalty)
- mpr-1_106, mpr-2_106, mpr-3_106 are all at distance 0 from each other

## Image Server (for Label Studio)

Start the CORS-enabled image server from the `results/previews/` directory:

```bash
cd results/previews
python3 ../../server.py 8081
```

This serves files from the current directory on port 8081 with CORS headers enabled.
The server must be running when importing tasks into Label Studio.