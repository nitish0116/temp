# PDF Forms Guide

Instructions for inspecting, filling, flattening, and creating PDF form
fields (AcroForms). Follow these steps whenever the user asks to fill out a
PDF form.

## Step 1: Inspect the form fields first

Never guess field names — read them from the file before filling.

```python
from pypdf import PdfReader

reader = PdfReader("form.pdf")
fields = reader.get_fields()

if not fields:
    print("No AcroForm fields found (may be a flat/scanned form).")
else:
    for name, field in fields.items():
        print(f"{name!r}: type={field.get('/FT')}, value={field.get('/V')}")
```

Common field types (`/FT`):
- `/Tx` — text field
- `/Btn` — button (checkbox / radio)
- `/Ch` — choice (dropdown / list)
- `/Sig` — signature

## Step 2: Fill text fields

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("form.pdf")
writer = PdfWriter()
writer.append(reader)

data = {
    "FullName": "Jane Doe",
    "Email": "jane@example.com",
    "Date": "2026-07-05",
}

for page in writer.pages:
    writer.update_page_form_field_values(page, data)

with open("filled.pdf", "wb") as f:
    writer.write(f)
```

## Step 3: Checkboxes and radio buttons

Checkbox "on" values are usually `/Yes` but can be custom (e.g. `/On`, `/1`).
Determine the exact on-state from the field's `/_States_` list.

```python
from pypdf import PdfReader

reader = PdfReader("form.pdf")
fields = reader.get_fields()
print(fields["AgreeToTerms"].get("/_States_"))   # e.g. ['/Yes', '/Off']
```

Then set it:
```python
writer.update_page_form_field_values(
    writer.pages[0],
    {"AgreeToTerms": "/Yes"},   # use the exact on-state string
)
```

For radio groups, set the parent field name to the value of the option to select.

## Step 4: Make filled values visible (NeedAppearances)

Some viewers won't render filled values unless the appearance flag is set.

```python
from pypdf.generic import NameObject, BooleanObject

writer.set_need_appearances_writer(True)   # pypdf >= 3.x helper
# Fallback if the helper is unavailable:
# writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
```

## Step 5: Flatten the form (optional)

Flattening bakes the values into the page so they can no longer be edited.

```python
# pypdf >= 4.x supports flattening on write:
writer.write("flattened.pdf")   # after setting flatten=True per page if supported

# Reliable cross-tool flatten via Ghostscript:
#   gs -sDEVICE=pdfwrite -dNOPAUSE -dBATCH -dQUIET \
#      -sOutputFile=flat.pdf filled.pdf
```

To flatten with pdftk:
```bash
pdftk filled.pdf output flattened.pdf flatten
```

## JavaScript: fill forms with pdf-lib

```javascript
import { PDFDocument } from "pdf-lib";
import fs from "fs";

const pdfDoc = await PDFDocument.load(fs.readFileSync("form.pdf"));
const form = pdfDoc.getForm();

form.getTextField("FullName").setText("Jane Doe");
form.getCheckBox("AgreeToTerms").check();
form.getDropdown("Country").select("India");

form.flatten();                     // optional: make read-only
fs.writeFileSync("filled.pdf", await pdfDoc.save());
```

## Creating a fillable form from scratch (ReportLab)

```python
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

c = canvas.Canvas("blank_form.pdf", pagesize=letter)
c.drawString(72, 720, "Full name:")
c.acroForm.textfield(
    name="FullName", tooltip="Full name",
    x=160, y=712, width=300, height=20, borderStyle="underlined",
)
c.drawString(72, 680, "Agree to terms:")
c.acroForm.checkbox(name="AgreeToTerms", x=160, y=678, size=16)
c.save()
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `get_fields()` returns `None` | No AcroForm (flat or scanned) | Re-create fields, or OCR + overlay text |
| Filled values invisible in some viewers | Missing appearances | `set_need_appearances_writer(True)` |
| Checkbox won't tick | Wrong on-state value | Read `/_States_` and use the exact name |
| Values editable after delivery | Not flattened | Flatten via pdf-lib, pdftk, or Ghostscript |

## See also
- `SKILL.md` — core PDF operations
- `REFERENCE.md` — advanced libraries and troubleshooting
