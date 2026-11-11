# Azure web app bundle

`webapp_bundle.py` defines the **curated** GenesysToolkit deploy (hub launcher + ops console + static mini-apps).

- **CI:** `scripts/stage_azure_webapp.py` → GitHub Actions → Azure App Service
- **IT zip:** `python scripts/package_azure_app_service.py [--env-file export.env]`

Secrets stay in Azure Application settings, not in the bundle.
