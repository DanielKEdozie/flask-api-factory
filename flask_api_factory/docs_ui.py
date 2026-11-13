"""HTML pages for the interactive API docs (Swagger UI / Redoc).

Assets are loaded from CDNs so the package stays small and works without a
build step. The pages point at the factory's OpenAPI JSON endpoint.
"""

from html import escape

_SWAGGER_CSS = "https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"
_SWAGGER_JS = "https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"
_REDOC_JS = "https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"


def swagger_page(title: str, spec_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="{_SWAGGER_CSS}">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{_SWAGGER_JS}"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: {spec_url!r},
      dom_id: '#swagger-ui',
      deepLinking: true,
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      layout: 'BaseLayout'
    }});
  </script>
</body>
</html>"""


def redoc_page(title: str, spec_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <script src="{_REDOC_JS}"></script>
</head>
<body>
  <redoc spec-url="{escape(spec_url)}"></redoc>
</body>
</html>"""
