# sankhya-api (relationship map)

This repo pulls Sankhya's published OpenAPI specs and generates a small JSON dataset that drives a local, interactive relationship graph.

## Prereqs

- Windows
- Python 3.13 (a `.venv` is already configured for this workspace)

## Usage

1) Download the OpenAPI specs

```powershell
C:/Users/tales/Documents/Projetos/sankhya-api/.venv/Scripts/python.exe scripts/fetch_specs.py
```

2) Build the normalized dataset (includes query params + response examples when present)

```powershell
C:/Users/tales/Documents/Projetos/sankhya-api/.venv/Scripts/python.exe scripts/build_dataset.py
```

3) Open the interactive viewer

Start a local server from the repo root (so the viewer can `fetch` the dataset):

```powershell
C:/Users/tales/Documents/Projetos/sankhya-api/.venv/Scripts/python.exe -m http.server 8000
```

Then open:

- http://localhost:8000/viewer/index.html

## Outputs

- `data/api.yaml`: Sankhya OpenAPI YAML
- `data/api-legada.yaml`: Sankhya legacy OpenAPI YAML
- `data/endpoints.json`: normalized dataset used by the viewer

## Notes

- The viewer draws edges based on shared tags and shared response schema component references.
