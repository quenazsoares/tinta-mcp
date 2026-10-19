"""Fixed document operations. Parameters are data, never Python source."""

import math
import re
from typing import Any

import inkex

from .common import create_success_response

_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_.:-]*")
_LOCAL_REF = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)")


def _id(value: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError(f"Invalid XML ID: {value!r}")
    return value


def _find(svg, element_id):
    matches = [element for element in svg.iter() if element.get("id") == element_id]
    if len(matches) > 1:
        raise ValueError(f"ID '{element_id}' is ambiguous because it occurs more than once")
    return matches[0] if matches else None


def _references(name, value):
    if not isinstance(value, str):
        return set()
    references = set(_LOCAL_REF.findall(value))
    if name in ("href", "xlink:href") and value.startswith("#"):
        references.add(value[1:])
    return references


def _has_stylesheet(svg) -> bool:
    if any(isinstance(node.tag, str) and node.tag.split("}")[-1] == "style" for node in svg.iter()):
        return True
    node = svg.getroottree().getroot().getprevious()
    while node is not None:
        if getattr(node, "target", None) == "xml-stylesheet":
            return True
        node = node.getprevious()
    return False


def create_group(svg, attributes: dict[str, Any]):
    group_id = _id(attributes["id"])
    if _find(svg, group_id) is not None:
        raise ValueError(f"ID '{group_id}' already exists")
    child_ids = [_id(child_id) for child_id in attributes.get("children_ids", [])]
    if len(child_ids) != len(set(child_ids)):
        raise ValueError("children_ids must not contain duplicates")
    children = [_find(svg, child_id) for child_id in child_ids]
    if any(child is None for child in children):
        raise ValueError("One or more child elements were not found")
    group = inkex.Group()
    group.set("id", group_id)
    if children:
        parent = children[0].getparent()
        if parent is None or any(child.getparent() is not parent for child in children):
            raise ValueError("Grouped elements must be siblings under the same parent")
        positions = [parent.index(child) for child in children]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError("Grouped elements must be contiguous siblings in paint order")
        if _has_stylesheet(svg):
            raise ValueError("Grouping is unsupported when the document contains CSS stylesheets")
        parent.insert(positions[0], group)
    else:
        parent = svg
        parent.append(group)
    for element in children:
        group.append(element)
    return create_success_response(
        f"Group '{group_id}' created with {len(children)} elements", id=group_id
    )


def modify_elements(svg, attributes: dict[str, Any]):
    items = attributes.get("elements", [])
    if not isinstance(items, list) or not items:
        raise ValueError("elements must be a non-empty list")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item.get("id")
        or not isinstance(item.get("attributes"), dict)
        or not item["attributes"]
        for item in items
    ):
        raise ValueError("Each element requires a non-empty id and attributes object")
    ids = [_id(item["id"]) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("elements must not contain duplicate IDs")
    elements = [_find(svg, element_id) for element_id in ids]
    if any(element is None for element in elements):
        raise ValueError("One or more elements to modify were not found")
    existing_ids = {element.get("id") for element in svg.iter() if element.get("id")}
    for item in items:
        if not isinstance(item.get("attributes"), dict):
            raise ValueError("Each element's attributes must be an object")
        if "id" in item["attributes"]:
            raise ValueError("Element IDs cannot be changed by modify-elements")
        for name, value in item["attributes"].items():
            missing_ids = _references(name, value) - existing_ids
            if missing_ids:
                raise ValueError(f"Referenced ID '{sorted(missing_ids)[0]}' does not exist")
    updated = []
    for item, element_id, element in zip(items, ids, elements, strict=True):
        for key, value in item["attributes"].items():
            if key == "text":
                element.text = str(value)
            else:
                element.set(key, str(value))
        updated.append(element_id)
    return create_success_response(
        f"Updated {len(updated)} element(s): {', '.join(updated)}", updated_ids=updated
    )


def delete_elements(svg, attributes: dict[str, Any]):
    raw_ids = attributes.get("ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError("ids must be a list")
    ids = [_id(element_id) for element_id in raw_ids]
    if not ids:
        raise ValueError("ids must be a non-empty list")
    if len(ids) != len(set(ids)):
        raise ValueError("ids must not contain duplicates")
    elements = [_find(svg, element_id) for element_id in ids]
    if any(element is None or element.getparent() is None for element in elements):
        raise ValueError("One or more elements to delete were not found")
    selected = set(elements)
    for element in elements:
        parent = element.getparent()
        while parent is not None:
            if parent in selected:
                raise ValueError("Cannot delete an element and its descendant together")
            parent = parent.getparent()
    removed_nodes = {node for element in elements for node in element.iter()}
    removed_ids = {node.get("id") for node in removed_nodes if node.get("id")}
    for node in svg.iter():
        if node not in removed_nodes:
            for name, value in node.attrib.items():
                if _references(name.split("}")[-1], value) & removed_ids:
                    raise ValueError("Cannot delete an element while another element references it")
    for element in elements:
        element.getparent().remove(element)
    deleted = ids
    return create_success_response(
        f"Deleted {len(deleted)} element(s): {', '.join(deleted)}", deleted_ids=deleted
    )


def apply_mask(svg, attributes: dict[str, Any]):
    target_id = _id(attributes["target_id"])
    target = _find(svg, target_id)
    if target is None:
        raise ValueError(f"Target element '{target_id}' not found")
    if attributes.get("remove", False):
        removed = []
        for key in ("clip-path", "mask"):
            if key in target.attrib:
                del target.attrib[key]
                removed.append(key)
        return create_success_response(
            f"Removed {', '.join(removed) if removed else 'no masks'} from '{target_id}'"
        )
    mask_id = _id(attributes["mask_id"])
    mode = attributes.get("mode", "clip")
    if mode not in ("clip", "luminance"):
        raise ValueError(f"Unsupported mask mode: {mode}")
    stencil = _find(svg, mask_id)
    if stencil is None:
        raise ValueError(f"Mask element '{mask_id}' not found")
    if target is stencil:
        raise ValueError("Target and mask elements must be different")
    if target in stencil.iter() or stencil in target.iter():
        raise ValueError("Target and stencil must not contain one another")
    if _has_stylesheet(svg):
        raise ValueError("Masks are unsupported when the document contains CSS stylesheets")
    for element in (target, stencil):
        node = element
        while node is not None and node is not svg:
            if node.get("transform"):
                raise ValueError("Masks are unsupported for transformed targets or stencils")
            if node is not element and (node.get("style") or node.get("class")):
                raise ValueError("Masks are unsupported when inherited styles may change")
            node = node.getparent()
    if any(node.get("transform") for node in stencil.iter()):
        raise ValueError("Masks are unsupported for transformed targets or stencils")
    if any(node.get("class") for node in stencil.iter()):
        raise ValueError("Masks are unsupported for class-styled stencils")
    is_clip = mode == "clip"
    container_id = f"{'clip' if is_clip else 'mask'}_{target_id}_{mask_id}"
    if _find(svg, container_id) is not None:
        raise ValueError(f"Mask container ID '{container_id}' already exists")
    container = inkex.ClipPath() if is_clip else inkex.Mask()
    container.set("id", container_id)
    if is_clip:
        container.set("clipPathUnits", "userSpaceOnUse")
    else:
        container.set("maskUnits", "userSpaceOnUse")
        container.set("maskContentUnits", "userSpaceOnUse")
    container.append(stencil)
    svg.defs.append(container)
    target.set("clip-path" if is_clip else "mask", f"url(#{container_id})")
    return create_success_response(
        f"Applied '{mode}' mask using '{mask_id}' onto '{target_id}' (container: {container_id})"
    )


def fit_canvas(svg, attributes: dict[str, Any]):
    padding = float(attributes.get("padding", 20))
    if not math.isfinite(padding) or padding < 0:
        raise ValueError("padding must be a finite non-negative number")
    bbox = None
    for element in svg:
        if hasattr(element, "bounding_box"):
            try:
                box = element.bounding_box()
                if box is not None:
                    bbox = bbox + box if bbox is not None else box
            except Exception:
                pass
    if bbox is None:
        return create_success_response("No bounding box found (document may be empty)")
    x, y = float(bbox.left) - padding, float(bbox.top) - padding
    width, height = float(bbox.width) + 2 * padding, float(bbox.height) + 2 * padding
    if (
        not all(math.isfinite(value) for value in (x, y, width, height))
        or width <= 0
        or height <= 0
    ):
        raise ValueError("Fitted canvas dimensions must be finite positive numbers")
    svg.set("viewBox", f"{x:.1f} {y:.1f} {width:.1f} {height:.1f}")
    svg.set("width", f"{width:.1f}")
    svg.set("height", f"{height:.1f}")
    return create_success_response(
        f"Canvas adjusted: viewBox='{svg.get('viewBox')}', dimensions={width:.1f}x{height:.1f}"
    )


def set_canvas(svg, attributes: dict[str, Any]):
    width, height = float(attributes["width"]), float(attributes["height"])
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("Canvas dimensions must be finite positive numbers")
    unit = attributes.get("unit", "px")
    if unit not in ("px", "mm", "cm", "in", "pt", "pc"):
        raise ValueError(f"Unsupported canvas unit: {unit}")
    suffix = "" if unit == "px" else unit
    svg.set("viewBox", f"0 0 {width} {height}")
    svg.set("width", f"{width}{suffix}")
    svg.set("height", f"{height}{suffix}")
    return create_success_response(f"Canvas size set to: {width}x{height} {unit}")


def clear_canvas(svg, attributes: dict[str, Any]):
    removed = [
        child
        for child in svg
        if isinstance(child.tag, str)
        and child.tag.split("}")[-1]
        not in ("defs", "namedview", "metadata", "title", "desc", "style")
    ]
    removed_nodes = {node for child in removed for node in child.iter()}
    removed_ids = {node.get("id") for node in removed_nodes if node.get("id")}
    for node in svg.iter():
        if node not in removed_nodes:
            for name, value in node.attrib.items():
                if _references(name.split("}")[-1], value) & removed_ids:
                    raise ValueError(
                        "Cannot clear elements that are referenced by retained content"
                    )
    count = 0
    for child in removed:
        svg.remove(child)
        count += 1
    return create_success_response(f"Canvas cleared: {count} elements removed", count=count)


DOCUMENT_OPERATIONS = {
    "create-group": create_group,
    "modify-elements": modify_elements,
    "delete-elements": delete_elements,
    "apply-mask": apply_mask,
    "fit-canvas": fit_canvas,
    "set-canvas": set_canvas,
    "clear-canvas": clear_canvas,
}
