# Proposal Agent

Generates professional client proposals from meeting transcripts or RFP context.

## Pipeline

1. **Draft** — analyzes input, retrieves specimen proposals, generates structured draft
2. **Pricing** — extracts scope parameters, applies pricing rubric, produces assessment
3. **Author** — writes to Google Doc (or Markdown) with revision markers
4. **Review** — revision loop with user feedback
5. **PDF** — source-aware finalization:
   - Google Docs export when review source is Google Docs
   - LaTeX for markdown source
   - WeasyPrint fallback when LaTeX is unavailable

## PDF stack (LaTeX vs WeasyPrint)

- **LaTeX** (optional, best quality): install a **system** TeX distribution so `xelatex` (or `pdflatex` / `lualatex`) is available; this is not a Python package.
- **WeasyPrint** (fallback when LaTeX is missing): the example declares `weasyprint` in `actions/jvagent/proposal_skill_interact_action/info.yaml`, so jvagent installs it when the action loads (unless `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL` is set). You can also `pip install weasyprint` or `pip install -r` the skill’s `requirements.txt` from the jvagent repo.

## Specimen Corpus

Add past proposals as Markdown files to `specimens/` to improve draft quality:

```
specimens/
├── README.md              # Corpus index with tags
├── template.md            # Proposal structure template
├── guide.md               # Writing principles
├── retail/                # Retail/e-commerce specimens
└── enterprise/            # Enterprise specimens
```

## Configuration

Key settings in `agent.yaml` under `proposal_skill_interact_action`:

| Setting | Description |
|---------|-------------|
| `specimens_path` | Path to specimen proposal corpus |
| `google_docs_template_id` | Optional branded template doc ID used for Google Docs authoring |
| `drive_specimens_folder_id` | Optional Google Drive folder for specimens |
| `drive_output_folder_id` | Optional Drive folder for final PDFs |
| `brand_logo_path` | Optional logo path/URL for PDF cover branding |
| `brand_primary_color` | Primary PDF brand color |
| `brand_accent_color` | Accent PDF brand color |
| `company_letterhead` | Optional cover-page letterhead text |

### Google Docs template recommendation

For highest-quality output, use a branded Google Docs template (letterhead, logo, styles, page settings) and set `GOOGLE_DOCS_TEMPLATE_ID`.
Fallback behavior:

- If template is configured and Google APIs are available: clone template and render draft content.
- If Google Docs is available without template: create blank doc and render formatted markdown blocks.
- If Google Docs is unavailable: write markdown to sandbox `output/`.

## Pricing Rubrics

Rubrics are managed via `PricingAction` API:

```bash
# List rubrics
curl -X GET http://localhost:8000/pricing/rubrics

# Create a custom rubric
curl -X POST http://localhost:8000/pricing/rubrics \
  -H "Content-Type: application/json" \
  -d '{"name": "startup", "base_rates": {"senior_engineer": 200, "engineer": 150}}'
```

## Usage

```
User: "I have a meeting transcript from Acme Corp. Generate a proposal."
Agent: "Let me analyze the transcript and generate a draft proposal..."
```
