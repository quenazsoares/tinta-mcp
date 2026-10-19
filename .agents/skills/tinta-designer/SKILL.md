---
name: tinta-designer
description: "Best practices, workflows, and technical guidelines for creating and editing vector graphics in Inkscape via Tinta MCP."
---

# Tinta designer workflow

Use this skill when creating, modifying, or inspecting vector illustrations, diagrams, icons, or layouts in an open Inkscape session.

## Core workflow

### 1. Inspect existing state
Before creating or editing graphics, verify the current canvas state:
- Call `get_document_info` to inspect canvas dimensions, viewBox coordinates, and existing element counts.
- Call `get_selection` if the user refers to "selected items", "this shape", or wants to modify whatever is currently highlighted in the Inkscape UI.
- Do not clear the canvas unless the user explicitly asks to start from scratch or clear the document.

### 2. Batch operations using `create_elements`
Avoid making multiple sequential tool calls to insert shapes one by one. Use `create_elements` to submit a structured batch:
- Combine element definitions, canvas fitting, and preview rendering in a single request.
- Use `fit_canvas=True` with `fit_padding` when drawing a composition whose final bounding box should define the page boundaries.
- Set `return_preview=True` to immediately obtain an image preview of the resulting render.

### 3. Verify with visual feedback
Always inspect the visual result before concluding a design task:
- Use the preview returned by `create_elements(return_preview=True)` or call `export_preview()`.
- Check for overlapping text, clipping at viewBox boundaries, or unintended layer stacking.
- If an adjustment is needed, modify elements by ID with `modify_elements` (using `return_preview=True` for instant visual verification) rather than redrawing the entire canvas.

## Technical SVG guidelines for Inkscape

### Document hierarchy and grouping
Organize elements into logical groups using `<g id="...">`:
- Group related shapes together (e.g. `header_group`, `chart_bars`, `icon_background`).
- Grouped elements appear cleanly in Inkscape's Objects and Layers panel (`Ctrl+Shift+L`), allowing the user to select, hide, or transform components easily.
- Assign meaningful, unique IDs to major elements so they can be modified later without guessing.

### Z-order and stacking
In SVG, rendering order follows document order:
- Elements defined earlier in the payload are drawn below later elements.
- Always declare background rectangles, cards, or base shapes first.
- Declare decorative details, strokes, icons, and text elements last.

### Primitive shapes versus paths
- Use native primitives (`<rect>`, `<circle>`, `<ellipse>`, `<line>`) whenever possible. In Inkscape, native primitives retain parametric handles (e.g. dragging corner radius on a rectangle).
- Use `<path>` for custom curves, logos, irregular polygons, or arrows where primitives are insufficient.

### Text positioning and baseline alignment
In SVG, the `y` coordinate of a `<text>` element refers to the typographic baseline, not the top-left bounding box corner:
- If placing text at the top of a container at `y=0`, the text will render above the visible area.
- Either offset `y` downwards by at least the font size (e.g. `y=24` for a `20px` font) or set `dominant-baseline="hanging"`.
- Use cross-platform fallback font stacks such as `system-ui, sans-serif, Arial, Helvetica, DejaVu Sans` so text renders reliably on both Windows and Linux installations.

### Viewport and coordinate consistency
- Work within a consistent coordinate system defined by the `viewBox`.
- When sizing rectangles or positioning icons, calculate relative offsets from the parent group or anchor position rather than placing arbitrary coordinates across the canvas.
