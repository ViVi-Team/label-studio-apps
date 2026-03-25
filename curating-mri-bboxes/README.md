# Brain Image Annotation with Label Studio

## Project Structure

```
labelstudio/
├── images/              # Place brain images here
├── annotations/         # Annotation output
├── scripts/
│   ├── convert_bboxes.py    # Convert existing bounding boxes to LS format
│   └── export_to_x1y1x2y2.py  # Convert LS export back to x1,y1,x2,y2
├── labeling_config.xml   # Label Studio template
├── launch.sh             # Launch helper (macOS/Linux)
└── launch.bat            # Launch helper (Windows)
```

## Setup

```bash
# Install Label Studio
pip install label-studio
```

## If You Have Existing Bounding Boxes

Your current format: `image_name,x1,y1,x2,y2,label,doctor` (normalized 0-1)

```bash
# Convert to Label Studio format
python scripts/convert_bboxes.py \
    /path/to/your/existing/bounding_boxes.csv \
    images/ \
    -o annotations/tasks.json

# Then import tasks.json when creating the project
```

## Usage

### Option 1: Using the launcher

**macOS / Linux:**
```bash
./launch.sh
```

**Windows:**
```cmd
launch.bat
```

### Option 2: Manual
```bash
label-studio start .
# Open http://localhost:8080
```

### Project Setup in Label Studio UI

1. Create new project → Name it "Brain Annotation"
2. Go to **Settings → Labeling Interface** → Paste contents of `labeling_config.xml`
3. Go to **Settings → Cloud Storage** (optional) or use **Import** to upload images
4. If pre-annotated: Import `annotations/tasks.json`

## Features

| Feature | How to Use |
|---------|------------|
| **Change box color** | Select the box → Choose label from sidebar (each label = different color) |
| **Move box** | Click and drag the existing box to reposition |
| **Resize box** | Drag the box edges/corners |
| **Delete box** | Select box → Press Delete/Backspace |
| **New box** | Click rectangle icon → Draw on image |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| 1 | Select label "N/A" |
| 2 | Select label "GCA" |
| 3 | Select label "Koedam" |
| 4 | Select label "MTA" |
| Delete | Remove selected box |
| Arrow keys | Nudge selected box |

## Export Annotations

When done, go to **Export** → Choose **JSON** format.

Then convert to your preferred format using:

```bash
# Convert LS export to x1,y1,x2,y2 normalized format
python3 scripts/export_to_x1y1x2y2.py \
    /path/to/label-studio-export.json \
    -o annotations_converted.csv

# Or JSON output
python3 scripts/export_to_x1y1x2y2.py \
    /path/to/label-studio-export.json \
    -o annotations_converted.json \
    --format json
```

**Output format:**
| Column | Description |
|--------|-------------|
| image_name | Filename |
| label | Selected label (N/A, GCA, Koedam, MTA) |
| x1 | Left edge (0-1 normalized) |
| y1 | Top edge (0-1 normalized) |
| x2 | Right edge (0-1 normalized) |
| y2 | Bottom edge (0-1 normalized) |
