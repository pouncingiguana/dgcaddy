#!/usr/bin/env python3
"""
Universal Disc Golf Caddy Book Generator
=========================================
Usage:
    python generate_caddybook.py disciples-backup.json

Outputs (all written next to where you run the script):
    <Course>_Caddy_Book.pdf          full-resolution 4-up PDF (unchanged settings)
    <Course>_Caddy_Book_lowres.pdf   smaller PDF (half resolution, JPEG-compressed)
    <Course>_Caddy_Book.html         phone-friendly OpenStreetMap version

Updates:
- Waypoints: holes with a "waypoints" list are drawn tee -> waypoints -> pin. The listed distance is still the straight tee-to-pin distance.
- Text Wrapping: Long OB rules automatically wrap into multi-line cards to prevent page overflow. Cover page title wraps cleanly for long names.
- Header Container: Top header uses a FancyBboxPatch bar to keep Par, Hole, and Distance neatly separated.
- Rotation Geometry Fix: R_stitch accounts for diagonal rotation, ensuring 100% satellite map coverage with no side bars.
"""

import sys
import os
import json
import math
import io
import textwrap
import requests
import numpy as np
from PIL import Image, ImageDraw
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.lib import colors

# Low-res PDF settings (full-res pages are 200 dpi; 0.5 -> 100 dpi)
LOWRES_SCALE = 0.5
LOWRES_JPEG_QUALITY = 70

HOLE_COLORS = ['#ef476f', '#f78c6c', '#ffd166', '#06d6a0', '#118ab2', '#38bdf8', '#8338ec', '#ea580c', '#ec4899',
               '#84cc16', '#a855f7', '#06b6d4', '#f43f5e', '#eab308', '#10b981', '#6366f1', '#d946ef', '#f97316']

# ------------------------------------------------------------------------------
# 1. GEOMETRY & MAP UTILITIES
# ------------------------------------------------------------------------------
R_EARTH_FT = 20902231.0
R_EARTH_M = 6371000.0

def haversine_distance(lat1, lon1, lat2, lon2, units="ft"):
    r = R_EARTH_M if units.lower() in ["m", "meters", "meter"] else R_EARTH_FT
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2.0)**2
    return round(r * (2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))))

def calculate_bearing(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1)*math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

def latlon_to_global_pixel(lat, lon, zoom=19):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n * 256.0
    y = (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n * 256.0
    return x, y

def rotate_point(px, py, cx, cy, angle_deg, new_w, new_h):
    rad = math.radians(angle_deg)
    tx, ty = px - cx, py - cy
    rx = tx * math.cos(rad) + ty * math.sin(rad)
    ry = -tx * math.sin(rad) + ty * math.cos(rad)
    return rx + new_w / 2.0, ry + new_h / 2.0

def get_path(h):
    """Ordered (lat, lon) list: tee -> any waypoints -> pin."""
    pts = [(h['tee']['lat'], h['tee']['lon'])]
    for w in (h.get('waypoints') or []):
        pts.append((w['lat'], w['lon']))
    pts.append((h['pin']['lat'], h['pin']['lon']))
    return pts

def polyline_midpoint(pts):
    """Point halfway along a polyline's length (pts are x, y tuples)."""
    seg = [math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1]) for i in range(len(pts) - 1)]
    half = sum(seg) / 2.0
    run = 0.0
    for i, s in enumerate(seg):
        if run + s >= half and s > 0:
            f = (half - run) / s
            return pts[i][0] + f * (pts[i+1][0] - pts[i][0]), pts[i][1] + f * (pts[i+1][1] - pts[i][1])
        run += s
    return pts[0]

def format_ob(h):
    ob_raw = h.get('ob', 'Standard course OB rules apply.')
    return ob_raw if ob_raw.lower().startswith("ob") else f"OB: {ob_raw}"

# ------------------------------------------------------------------------------
# 2. SATELLITE TILE FETCHING
# ------------------------------------------------------------------------------
def fetch_satellite_crop_pixel_box(min_x, max_x, min_y, max_y, zoom=19, padding_px=0):
    min_x_pad, max_x_pad = min_x - padding_px, max_x + padding_px
    min_y_pad, max_y_pad = min_y - padding_px, max_y + padding_px
    
    tx_min, tx_max = int(min_x_pad // 256), int(max_x_pad // 256)
    ty_min, ty_max = int(min_y_pad // 256), int(max_y_pad // 256)
    
    canvas_w = (tx_max - tx_min + 1) * 256
    canvas_h = (ty_max - ty_min + 1) * 256
    stitch_img = Image.new('RGB', (canvas_w, canvas_h), (30, 40, 30))
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{ty}/{tx}"
            try:
                res = requests.get(url, headers=headers, timeout=4)
                if res.status_code == 200:
                    tile = Image.open(io.BytesIO(res.content)).convert('RGB')
                    stitch_img.paste(tile, ((tx - tx_min) * 256, (ty - ty_min) * 256))
            except Exception:
                pass
                
    crop_x1, crop_y1 = int(min_x_pad - tx_min * 256), int(min_y_pad - ty_min * 256)
    crop_w, crop_h = int(max_x_pad - min_x_pad), int(max_y_pad - min_y_pad)
    cropped = stitch_img.crop((crop_x1, crop_y1, crop_x1 + crop_w, crop_y1 + crop_h))
    return cropped, min_x_pad, min_y_pad

# ------------------------------------------------------------------------------
# 3. PAGE RENDERERS
# ------------------------------------------------------------------------------
def generate_cover_page(course_name, total_holes, total_par, total_dist, units, output_path):
    fig, ax = plt.subplots(figsize=(4.25, 5.5), dpi=200)
    fig.patch.set_facecolor('#0f172a')
    ax.set_facecolor('#0f172a')
    ax.axis('off')
    
    # Wrap long course names across multiple lines
    wrapped_title = textwrap.fill(course_name.upper(), width=16)
    
    ax.text(0.5, 0.81, wrapped_title, color='#38bdf8', fontsize=18, fontweight='bold', ha='center', va='center', transform=ax.transAxes, linespacing=1.1)
    ax.text(0.5, 0.69, "DISC GOLF COURSE", color='#f8fafc', fontsize=10, fontweight='bold', ha='center', transform=ax.transAxes)
    ax.text(0.5, 0.64, "CADDY BOOK", color='#94a3b8', fontsize=9, fontweight='bold', ha='center', transform=ax.transAxes)
    
    circle_bg = patches.Circle((0.5, 0.45), 0.13, transform=ax.transAxes, color='#1e293b', ec='#38bdf8', lw=2)
    ax.add_patch(circle_bg)
    ax.text(0.5, 0.45, "🥏", fontsize=30, ha='center', va='center', transform=ax.transAxes)
    
    stats_text = f"{total_holes} HOLES   •   PAR {total_par}\nTOTAL DISTANCE: {total_dist:,} {units.upper()}"
    bbox_p = dict(boxstyle="round,pad=0.8", fc="#1e293b", ec="#38bdf8", lw=1.5)
    ax.text(0.5, 0.20, stats_text, color='#f1f5f9', fontsize=9, fontweight='bold', ha='center', va='center', bbox=bbox_p, transform=ax.transAxes, linespacing=1.6)
    
    plt.tight_layout()
    plt.savefig(output_path, facecolor=fig.get_facecolor(), dpi=200)
    plt.close()

def generate_overview_page(holes, output_path):
    zoom = 18
    g_coords = []
    for h in holes:
        for la, lo in get_path(h):
            g_coords.append(latlon_to_global_pixel(la, lo, zoom))
        
    xs = [c[0] for c in g_coords]
    ys = [c[1] for c in g_coords]
    
    bg_img, min_x, min_y = fetch_satellite_crop_pixel_box(min(xs), max(xs), min(ys), max(ys), zoom=zoom, padding_px=120)
    
    fig, ax = plt.subplots(figsize=(4.25, 5.5), dpi=200)
    img_h, img_w = bg_img.size[1], bg_img.size[0]
    ax.imshow(bg_img)
    ax.set_xlim(0, img_w)
    ax.set_ylim(img_h, 0)
    ax.axis('off')
    
    for h in holes:
        pts = [(gx - min_x, gy - min_y) for gx, gy in
               (latlon_to_global_pixel(la, lo, zoom) for la, lo in get_path(h))]
        tx_loc, ty_loc = pts[0]
        px_loc, py_loc = pts[-1]
        c = HOLE_COLORS[(h['n'] - 1) % len(HOLE_COLORS)]
        
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, linewidth=2.5, alpha=0.9, zorder=3,
                solid_joinstyle='round')
        ax.plot(tx_loc, ty_loc, 's', color=c, markersize=5, zorder=4)
        ax.plot(px_loc, py_loc, 'o', color=c, markersize=6, markeredgecolor='white', markeredgewidth=1, zorder=4)
        
        mx, my = polyline_midpoint(pts)
        ax.text(mx, my, str(h['n']), color='white', fontsize=7, fontweight='bold', ha='center', va='center',
                bbox=dict(boxstyle="circle,pad=0.2", fc=c, ec="white", lw=1), zorder=5)
        
    ax.text(img_w * 0.5, img_h * 0.06, "COURSE OVERVIEW", color='white', fontsize=13, fontweight='bold', ha='center', va='center',
            bbox=dict(boxstyle="round,pad=0.4", fc="#0f172a", ec="#38bdf8", lw=1.5, alpha=0.88), zorder=10)
    
    ax.annotate('N', xy=(img_w * 0.90, img_h * 0.12), xytext=(img_w * 0.90, img_h * 0.18),
                arrowprops=dict(facecolor='#38bdf8', edgecolor='white', width=2, headwidth=6),
                ha='center', va='center', fontsize=8, fontweight='bold', color='white', zorder=10)
    
    plt.tight_layout()
    plt.savefig(output_path, facecolor='#0f172a', bbox_inches='tight', pad_inches=0)
    plt.close()

def generate_hole_page(h, units, output_path):
    zoom = 19
    path_g = [latlon_to_global_pixel(la, lo, zoom) for la, lo in get_path(h)]
    x1_g, y1_g = path_g[0]
    x2_g, y2_g = path_g[-1]
    xm_g, ym_g = (x1_g + x2_g) / 2.0, (y1_g + y2_g) / 2.0
    
    # Page is rotated so tee -> pin points "up". Work out the rotated extent of the
    # WHOLE path (tee, waypoints, pin) so waypoints that swing wide stay on the page.
    bearing_deg = calculate_bearing(h['tee']['lat'], h['tee']['lon'], h['pin']['lat'], h['pin']['lon'])
    rad = math.radians(bearing_deg)
    cos_b, sin_b = math.cos(rad), math.sin(rad)
    rotated = [((x - xm_g) * cos_b + (y - ym_g) * sin_b,
                -(x - xm_g) * sin_b + (y - ym_g) * cos_b) for x, y in path_g]
    min_rx, max_rx = min(r[0] for r in rotated), max(r[0] for r in rotated)
    min_ry, max_ry = min(r[1] for r in rotated), max(r[1] for r in rotated)
    ext_w, ext_h = max_rx - min_rx, max_ry - min_ry
    
    aspect = 4.25 / 5.5
    box_h = max(650.0, ext_h + 350.0, (ext_w + 250.0) / aspect)
    box_w = box_h * aspect
    
    # Center the page on the middle of the path's rotated bounding box (== tee/pin midpoint
    # for a straight hole), converted back into global pixel space.
    ocx, ocy = (min_rx + max_rx) / 2.0, (min_ry + max_ry) / 2.0
    xc_g = xm_g + ocx * cos_b - ocy * sin_b
    yc_g = ym_g + ocx * sin_b + ocy * cos_b
    
    # Safe R_stitch: accounts for diagonal rotation geometry
    R_stitch = math.hypot(box_w / 2.0, box_h / 2.0) * math.sqrt(2) + 120.0
    
    min_x_g, max_x_g = xc_g - R_stitch, xc_g + R_stitch
    min_y_g, max_y_g = yc_g - R_stitch, yc_g + R_stitch
    
    stitch_img, min_x_pad, min_y_pad = fetch_satellite_crop_pixel_box(
        min_x_g, max_x_g, min_y_g, max_y_g, zoom=zoom, padding_px=0
    )
    
    w_crop, h_crop = stitch_img.size
    rot_img = stitch_img.rotate(bearing_deg, resample=Image.BICUBIC, expand=True)
    new_w, new_h = rot_img.size
    cx, cy = w_crop / 2.0, h_crop / 2.0
    
    rot_xm, rot_ym = rotate_point(cx, cy, cx, cy, bearing_deg, new_w, new_h)
    
    crop_x1 = int(rot_xm - box_w / 2.0)
    crop_y1 = int(rot_ym - box_h / 2.0)
    crop_x2 = int(rot_xm + box_w / 2.0)
    crop_y2 = int(rot_ym + box_h / 2.0)
    
    final_crop = rot_img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
    final_crop = final_crop.resize((850, 1100), Image.LANCZOS)
    
    scale_x, scale_y = 850.0 / box_w, 1100.0 / box_h
    local_pts = []
    for gx, gy in path_g:
        rx, ry = rotate_point(gx - min_x_pad, gy - min_y_pad, cx, cy, bearing_deg, new_w, new_h)
        local_pts.append(((rx - crop_x1) * scale_x, (ry - crop_y1) * scale_y))
    t_local, p_local = local_pts[0], local_pts[-1]
    
    fig, ax = plt.subplots(figsize=(4.25, 5.5), dpi=200)
    img_h, img_w = final_crop.size[1], final_crop.size[0]
    ax.imshow(final_crop)
    ax.set_xlim(0, img_w)
    ax.set_ylim(img_h, 0)
    ax.axis('off')
    
    # Flight Line (tee -> waypoints -> pin)
    ax.plot([p[0] for p in local_pts], [p[1] for p in local_pts], color='#fbbf24', linestyle='--',
            linewidth=3, zorder=3, solid_joinstyle='round', dash_joinstyle='round')
    
    # Waypoint markers
    for wx, wy in local_pts[1:-1]:
        ax.plot(wx, wy, marker='o', markersize=6, color='#fbbf24', markeredgecolor='white', markeredgewidth=1.2, zorder=4)
    
    # Tee Marker & Label
    ax.plot(t_local[0], t_local[1], marker='s', markersize=10, color='#f97316', markeredgecolor='white', markeredgewidth=1.5, zorder=4)
    ax.text(t_local[0], t_local[1] + 25, "TEE", color='white', fontsize=7, fontweight='bold', ha='center', va='top', zorder=5,
            bbox=dict(boxstyle="round,pad=0.2", fc="#0f172a", ec="none", alpha=0.7))
    
    # Pin Marker & Label
    ax.plot(p_local[0], p_local[1], marker='o', markersize=11, color='#ef476f', markeredgecolor='white', markeredgewidth=1.5, zorder=4)
    ax.text(p_local[0], p_local[1] - 25, "PIN", color='white', fontsize=7, fontweight='bold', ha='center', va='bottom', zorder=5,
            bbox=dict(boxstyle="round,pad=0.2", fc="#0f172a", ec="none", alpha=0.7))
    
    # Top Header Container
    header_fancy = FancyBboxPatch((img_w * 0.04, img_h * 0.02), img_w * 0.92, img_h * 0.07,
                                  boxstyle="round,pad=0.01,rounding_size=15", fc="#0f172a", ec="#38bdf8", lw=1.5, alpha=0.9, zorder=9)
    ax.add_patch(header_fancy)
    
    ax.text(img_w * 0.50, img_h * 0.055, f"HOLE {h['n']}", color='white', fontsize=14, fontweight='bold', ha='center', va='center', zorder=10)
    ax.text(img_w * 0.08, img_h * 0.055, f"PAR {h.get('par', 3)}", color='#fde047', fontsize=9, fontweight='bold', ha='left', va='center', zorder=10)
    ax.text(img_w * 0.92, img_h * 0.055, f"{h['dist']} {units.upper()}", color='#34d399', fontsize=9, fontweight='bold', ha='right', va='center', zorder=10)
    
    # Rotated North Arrow
    north_rad = math.radians(-bearing_deg - 90)
    arrow_len = 35
    nx0, ny0 = img_w * 0.88, img_h * 0.15
    nx1, ny1 = nx0 + arrow_len * math.cos(north_rad), ny0 + arrow_len * math.sin(north_rad)
    ax.annotate('N', xy=(nx1, ny1), xytext=(nx0, ny0),
                arrowprops=dict(facecolor='#38bdf8', edgecolor='white', width=1.5, headwidth=5),
                ha='center', va='center', fontsize=7, fontweight='bold', color='white', zorder=10)
    
    # Wrapped OB Callout Box
    ob_str = format_ob(h)
    wrapped_ob = textwrap.fill(ob_str, width=32)
    ob_box = dict(boxstyle="round,pad=0.5", fc="#0f172a", ec="#ef476f", lw=1.5, alpha=0.92)
    ax.text(img_w * 0.5, img_h * 0.90, wrapped_ob, color='#f8fafc', fontsize=8, fontweight='bold', ha='center', va='center', bbox=ob_box, zorder=10, linespacing=1.25)
    
    plt.tight_layout()
    plt.savefig(output_path, facecolor='#0f172a', bbox_inches='tight', pad_inches=0)
    plt.close()

# ------------------------------------------------------------------------------
# 4. BUILD PDF IMPOSITION (4-UP GRID)
# ------------------------------------------------------------------------------
def build_caddybook_pdf(page_images, pdf_filename, lowres=False):
    """Full-res: embeds the page PNGs as-is (original behaviour).
    Low-res: downsamples each page by LOWRES_SCALE and embeds it as a JPEG."""
    c = canvas.Canvas(pdf_filename, pagesize=letter)
    width, height = letter
    hw, hh = width / 2.0, height / 2.0
    
    quads = [
        (0, hh, hw, hh),   # Top-Left
        (hw, hh, hw, hh),  # Top-Right
        (0, 0, hw, hh),   # Bottom-Left
        (hw, 0, hw, hh)   # Bottom-Right
    ]
    
    keep_alive = []  # keep in-memory JPEG buffers around until c.save()
    
    def load_page(path):
        if not lowres:
            return path
        with Image.open(path) as im:
            im = im.convert('RGB')
            new_size = (max(1, int(im.width * LOWRES_SCALE)), max(1, int(im.height * LOWRES_SCALE)))
            im = im.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=LOWRES_JPEG_QUALITY, optimize=True)
        buf.seek(0)
        keep_alive.append(buf)
        return ImageReader(buf)
    
    total_pages = len(page_images)
    total_sheets = math.ceil(total_pages / 4.0)
    
    for sheet in range(total_sheets):
        for q in range(4):
            page_num = sheet * 4 + q + 1
            if page_num in page_images:
                qx, qy, qw, qh = quads[q]
                c.drawImage(load_page(page_images[page_num]), qx, qy, width=qw, height=qh)
        
        # Cut Guidelines
        c.saveState()
        c.setStrokeColor(colors.HexColor("#94a3b8"))
        c.setLineWidth(0.75)
        c.setDash(4, 4)
        c.line(hw, 0, hw, height)
        c.line(0, hh, width, hh)
        c.restoreState()
        
        c.showPage()
        
    c.save()
    size_mb = os.path.getsize(pdf_filename) / (1024.0 * 1024.0)
    label = "Low-res Caddy Book PDF" if lowres else "Caddy Book PDF"
    print(f"\n✅ {label} created successfully: {pdf_filename} ({size_mb:.1f} MB)")

# ------------------------------------------------------------------------------
# 4b. PHONE-FRIENDLY HTML (OpenStreetMap via Leaflet)
# ------------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>__TITLE__ Caddy Book</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
:root {
  --navy: #0f172a; --panel: #1e293b; --line: #334155;
  --cyan: #38bdf8; --amber: #fbbf24; --orange: #f97316; --pink: #ef476f;
  --text: #f8fafc; --muted: #94a3b8; --green: #34d399; --yellow: #fde047;
  --card-h: 0px;
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { height: 100%; margin: 0; background: var(--navy); color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  overflow: hidden; overscroll-behavior: none; }
button { font: inherit; color: inherit; touch-action: manipulation; }

#map { position: absolute; inset: 0; background: #e5e3df; }
/* keep the OpenStreetMap credit visible above the info card */
.leaflet-bottom { bottom: var(--card-h); }

.top { position: absolute; z-index: 1000; top: 0; left: 0; right: 0;
  padding: calc(env(safe-area-inset-top, 0px) + 8px) 0 8px;
  background: rgba(15, 23, 42, .92); border-bottom: 1px solid var(--line); }
.chips { display: flex; gap: 6px; overflow-x: auto; padding: 0 10px; scrollbar-width: none; }
.chips::-webkit-scrollbar { display: none; }
.chip { flex: 0 0 auto; min-width: 44px; height: 44px; padding: 0 12px; border-radius: 22px;
  border: 1px solid var(--line); background: var(--panel); font-weight: 700; font-size: 16px; }
.chip.on { background: var(--cyan); border-color: var(--cyan); color: var(--navy); }

.compass { position: absolute; z-index: 1000; right: 12px;
  top: calc(env(safe-area-inset-top, 0px) + 72px); width: 40px; height: 40px; pointer-events: none; }
.compass svg { display: block; transition: transform .25s ease; }
.card { position: absolute; z-index: 1000; left: 0; right: 0; bottom: 0;
  padding: 12px 14px calc(env(safe-area-inset-bottom, 0px) + 12px);
  background: rgba(15, 23, 42, .96); border-top: 2px solid var(--cyan);
  border-radius: 18px 18px 0 0; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; text-align: center; }
.stat b { display: block; font-size: 28px; line-height: 1.1; font-weight: 800; }
.stat span { font-size: 13px; color: var(--muted); }
.stat.par b { color: var(--yellow); }
.stat.dist b { color: var(--green); }
.ob { margin: 10px 0 0; padding: 9px 12px; background: var(--panel); border-left: 4px solid var(--pink);
  border-radius: 8px; font-size: 15px; line-height: 1.35; font-weight: 600; }
.ob:empty { display: none; }
.hint { margin: 10px 0 0; font-size: 14px; color: var(--muted); text-align: center; }
.nav { display: flex; gap: 10px; margin-top: 12px; }
.nav button { flex: 1; height: 48px; border-radius: 12px; border: 1px solid var(--line);
  background: var(--panel); font-weight: 700; font-size: 16px; }
.nav button:disabled { opacity: .35; }
.nav button.next { background: var(--cyan); border-color: var(--cyan); color: var(--navy); }
.nav button.next:disabled { background: var(--panel); color: var(--text); border-color: var(--line); }

/* map markers */
.pt { position: relative; width: 0; height: 0; }
.pt i { position: absolute; left: 0; top: 0; transform: translate(-50%, -50%); display: block;
  border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.55); }
.pt b { position: absolute; left: 0; top: 15px; transform: translateX(-50%); white-space: nowrap;
  font: 700 12px/1 system-ui, sans-serif; color: #fff; background: rgba(15,23,42,.85);
  padding: 4px 7px; border-radius: 7px; }
.pt.tee i { width: 20px; height: 20px; background: var(--orange); border-radius: 4px; }
.pt.pin i { width: 22px; height: 22px; background: var(--pink); border-radius: 50%; }
.pt.pin b { top: -36px; }
.pt.wp i { width: 12px; height: 12px; background: var(--amber); border-radius: 50%; }
.pt.num i { width: 30px; height: 30px; border-radius: 50%; background: var(--c); display: flex;
  align-items: center; justify-content: center; font: 800 14px/1 system-ui, sans-serif; color: #fff;
  text-shadow: 0 1px 2px rgba(0,0,0,.6); }
.pt.dot i { width: 10px; height: 10px; background: var(--c); border-radius: 2px; border-width: 1.5px; }
</style>
</head>
<body>
<div id="map"></div>
<div class="top"><div class="chips" id="chips" role="tablist" aria-label="Holes"></div></div>
<div class="compass" id="compass" aria-label="North">
  <svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="19" fill="#0f172a" fill-opacity=".88" stroke="#38bdf8" stroke-width="1.5"/>
  <path d="M20 5 L26 22 L20 18.5 L14 22 Z" fill="#38bdf8" stroke="#fff" stroke-width="1"/>
  <text x="20" y="34" text-anchor="middle" font-family="system-ui,sans-serif" font-size="10" font-weight="800" fill="#fff">N</text></svg>
</div>
<div class="card" id="card">
  <div class="stats" id="stats"></div>
  <p class="ob" id="ob"></p>
  <p class="hint" id="hint"></p>
  <div class="nav">
    <button id="prev" type="button">Previous</button>
    <button id="next" type="button" class="next">Next</button>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/leaflet-rotate@0.2.8/dist/leaflet-rotate.js"></script>
<script>
const D = __DATA__;
const COLORS = __COLORS__;

const map = L.map('map', { zoomControl: false, maxZoom: 20, zoomSnap: 0.25,
  rotate: true, bearing: 0, touchRotate: false, rotateControl: false });
const CAN_ROTATE = typeof map.setBearing === 'function' && L.Browser.any3d;
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxNativeZoom: 19, maxZoom: 20,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
map.attributionControl.setPrefix(false);
const layer = L.layerGroup().addTo(map);

const $ = id => document.getElementById(id);
let cur = 0;   // 0 = course overview, 1..N = hole number

function icon(cls, label, extra) {
  return L.divIcon({ className: 'pt ' + cls, iconSize: [0, 0],
    html: '<i' + (extra || '') + '></i>' + (label ? '<b>' + label + '</b>' : '') });
}

function pathMidpoint(path) {
  const pts = path.map(p => L.latLng(p[0], p[1]));
  const seg = [];
  let total = 0;
  for (let i = 0; i < pts.length - 1; i++) { const d = pts[i].distanceTo(pts[i + 1]); seg.push(d); total += d; }
  let run = 0;
  for (let i = 0; i < seg.length; i++) {
    if (run + seg[i] >= total / 2 && seg[i] > 0) {
      const f = (total / 2 - run) / seg[i];
      return [pts[i].lat + f * (pts[i + 1].lat - pts[i].lat), pts[i].lng + f * (pts[i + 1].lng - pts[i].lng)];
    }
    run += seg[i];
  }
  return path[0];
}

function stat(cls, value, label) {
  return '<div class="stat ' + cls + '"><b>' + value + '</b><span>' + label + '</span></div>';
}

function drawOverview() {
  const all = [];
  D.holes.forEach(h => {
    const c = COLORS[(h.n - 1) % COLORS.length];
    L.polyline(h.path, { color: '#fff', weight: 7, opacity: .7, interactive: false }).addTo(layer);
    L.polyline(h.path, { color: c, weight: 4, opacity: 1, interactive: false }).addTo(layer);
    L.marker(h.path[0], { icon: icon('dot', '', ' style="--c:' + c + '"'), interactive: false }).addTo(layer);
    L.marker(pathMidpoint(h.path), {
      icon: L.divIcon({ className: 'pt num', iconSize: [0, 0],
        html: '<i style="--c:' + c + '">' + h.n + '</i>' }),
      keyboard: false, title: 'Hole ' + h.n
    }).on('click', () => go(h.n)).addTo(layer);
    h.path.forEach(p => all.push(p));
  });
  $('stats').innerHTML = stat('', D.holes.length, 'Holes') + stat('par', D.totalPar, 'Par') +
    stat('dist', D.totalDist.toLocaleString() + ' ' + D.units, 'Total');
  $('ob').textContent = '';
  $('hint').textContent = 'Tap a hole number to open it.';
  return all;
}

function drawHole(h) {
  L.polyline(h.path, { color: '#fff', weight: 8, opacity: .75, interactive: false }).addTo(layer);
  L.polyline(h.path, { color: '#d97706', weight: 4, dashArray: '9 8', opacity: 1, interactive: false }).addTo(layer);
  h.path.slice(1, -1).forEach(p => L.marker(p, { icon: icon('wp'), interactive: false }).addTo(layer));
  L.marker(h.path[0], { icon: icon('tee', 'Tee'), interactive: false }).addTo(layer);
  L.marker(h.path[h.path.length - 1], { icon: icon('pin', 'Pin'), interactive: false }).addTo(layer);
  $('stats').innerHTML = stat('', 'Hole ' + h.n, '&nbsp;') + stat('par', h.par, 'Par') +
    stat('dist', h.dist.toLocaleString() + ' ' + D.units, 'Tee to pin');
  $('ob').textContent = h.ob;
  $('hint').textContent = '';
  return h.path;
}

function render() {
  layer.clearLayers();
  document.querySelectorAll('.chip').forEach((el, i) => {
    el.classList.toggle('on', i === cur);
    el.setAttribute('aria-selected', i === cur);
  });
  const pts = cur === 0 ? drawOverview() : drawHole(D.holes[cur - 1]);
  const bearing = cur === 0 ? null : D.holes[cur - 1].bearing;
  $('compass').firstElementChild.style.transform = 'rotate(' + (bearing === null || !CAN_ROTATE ? 0 : -bearing) + 'deg)';
  $('prev').disabled = cur === 0;
  $('next').disabled = cur === D.holes.length;
  document.documentElement.style.setProperty('--card-h', $('card').offsetHeight + 'px');
  fit(pts, bearing);
  const on = document.querySelector('.chip.on');
  if (on) on.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
  history.replaceState(null, '', '#' + cur);
}

function fit(pts, bearing) {
  map.invalidateSize();
  const size = map.getSize();
  const side = 24;
  const top = $('chips').parentElement.offsetHeight + 28;
  const bottom = $('card').offsetHeight + 36;

  // Overview (or no rotation support): north-up, fit the bounding box.
  if (bearing === null || !CAN_ROTATE) {
    if (CAN_ROTATE) map.setBearing(0);
    map.fitBounds(L.latLngBounds(pts), { paddingTopLeft: [side, top], paddingBottomRight: [side, bottom],
      maxZoom: 20, animate: false });
    return;
  }

  // Hole view: rotate the map so tee -> pin points straight up (tee at the bottom,
  // pin at the top), then pick the zoom/center that fits the whole tee-waypoints-pin path.
  map.setBearing(-bearing);
  const rad = bearing * Math.PI / 180, c = Math.cos(rad), s = Math.sin(rad);
  const P = pts.map(p => map.project(L.latLng(p[0], p[1]), 0));   // world pixels at zoom 0
  const o = P[0];
  const R = P.map(p => { const dx = p.x - o.x, dy = p.y - o.y; return [dx * c + dy * s, -dx * s + dy * c]; });
  const minX = Math.min(...R.map(r => r[0])), maxX = Math.max(...R.map(r => r[0]));
  const minY = Math.min(...R.map(r => r[1])), maxY = Math.max(...R.map(r => r[1]));
  const availW = Math.max(50, size.x - 2 * side), availH = Math.max(50, size.y - top - bottom);
  const scale = Math.min(availW / Math.max(maxX - minX, 1e-9), availH / Math.max(maxY - minY, 1e-9));
  let z = Math.floor(Math.log2(scale) * 4) / 4;           // snap down to a quarter zoom level
  z = Math.min(20, z);
  const k = Math.pow(2, z);
  // Put the middle of the path in the middle of the free area (between the chip bar and the card).
  const rcx = (minX + maxX) / 2, rcy = (minY + maxY) / 2 - (top + availH / 2 - size.y / 2) / k;
  const dx = rcx * c - rcy * s, dy = rcx * s + rcy * c;      // back to unrotated world pixels
  map.setView(map.unproject(L.point(o.x + dx, o.y + dy), 0), z, { animate: false });
}

function go(n) { cur = Math.max(0, Math.min(D.holes.length, n)); render(); }

(function init() {
  const chips = $('chips');
  ['All'].concat(D.holes.map(h => String(h.n))).forEach((label, i) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip'; b.textContent = label;
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-label', i === 0 ? 'Course overview' : 'Hole ' + label);
    b.addEventListener('click', () => go(i));
    chips.appendChild(b);
  });
  $('prev').addEventListener('click', () => go(cur - 1));
  $('next').addEventListener('click', () => go(cur + 1));
  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft') go(cur - 1);
    if (e.key === 'ArrowRight') go(cur + 1);
  });
  window.addEventListener('resize', () => go(cur));
  const start = parseInt((location.hash || '').slice(1), 10);
  cur = isNaN(start) ? 0 : Math.max(0, Math.min(D.holes.length, start));
  map.setView(D.holes[0].path[0], 17, { animate: false });
  render();
})();
</script>
</body>
</html>
"""

def generate_html_page(course_name, units, holes, total_par, total_dist, html_filename):
    data = {
        "course": course_name,
        "units": units,
        "totalPar": total_par,
        "totalDist": total_dist,
        "holes": [{
            "n": h['n'],
            "par": h.get('par', 3),
            "dist": h['dist'],
            "ob": format_ob(h),
            "bearing": round(h['bearing'], 2),   # tee -> pin, degrees clockwise from north
            # tee, then any waypoints, then pin
            "path": [[round(la, 7), round(lo, 7)] for la, lo in get_path(h)],
        } for h in holes],
    }
    # "</" inside JSON could close the <script> tag early, so escape it
    data_js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = (course_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    page = (HTML_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__COLORS__", json.dumps(HOLE_COLORS))
            .replace("__DATA__", data_js))
    with open(html_filename, "w", encoding="utf-8") as f:
        f.write(page)
    size_kb = os.path.getsize(html_filename) / 1024.0
    print(f"\n✅ Phone-friendly HTML map created successfully: {html_filename} ({size_kb:.0f} KB)")

# ------------------------------------------------------------------------------
# 5. MAIN EXECUTION
# ------------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_caddybook.py <path_to_course_json>")
        sys.exit(1)
        
    json_path = sys.argv[1]
    if not os.path.exists(json_path):
        print(f"Error: File not found '{json_path}'")
        sys.exit(1)
        
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    course_name = data.get("courseName", "Disc Golf Course")
    units = data.get("units", "ft")
    holes = data.get("holes", [])
    
    if not holes:
        print("Error: No hole data found in JSON.")
        sys.exit(1)
        
    total_dist = 0
    total_par = 0
    for h in holes:
        h['dist'] = haversine_distance(
            h['tee']['lat'], h['tee']['lon'],
            h['pin']['lat'], h['pin']['lon'],
            units=units
        )
        h['bearing'] = calculate_bearing(
            h['tee']['lat'], h['tee']['lon'],
            h['pin']['lat'], h['pin']['lon']
        )
        total_dist += h['dist']
        total_par += h.get('par', 3)
        
    print(f"🛰 Processing '{course_name}' ({len(holes)} holes, Par {total_par}, {total_dist} {units})...")
    
    page_imgs = {}
    
    # Page 1: Cover Page
    generate_cover_page(course_name, len(holes), total_par, total_dist, units, "temp_page_01.png")
    page_imgs[1] = "temp_page_01.png"
    
    # Page 2: Satellite Course Overview
    print("  • Generating Satellite Course Overview map...")
    generate_overview_page(holes, "temp_page_02.png")
    page_imgs[2] = "temp_page_02.png"
    
    # Pages 3+: Individual Hole Maps
    for h in holes:
        p_num = h['n'] + 2
        img_filename = f"temp_page_{p_num:02d}.png"
        print(f"  • Generating Hole {h['n']} satellite map & text overlays...")
        generate_hole_page(h, units, img_filename)
        page_imgs[p_num] = img_filename
        
    # Build outputs
    clean_name = "".join(c if c.isalnum() else "_" for c in course_name)
    pdf_filename = f"{clean_name}_Caddy_Book.pdf"
    lowres_filename = f"{clean_name}_Caddy_Book_lowres.pdf"
    html_filename = f"{clean_name}_Caddy_Book.html"
    
    build_caddybook_pdf(page_imgs, pdf_filename)                   # full-res (unchanged)
    build_caddybook_pdf(page_imgs, lowres_filename, lowres=True)   # small file
    generate_html_page(course_name, units, holes, total_par, total_dist, html_filename)
    
    # Clean up temporary PNG files
    for p_file in page_imgs.values():
        if os.path.exists(p_file):
            os.remove(p_file)

if __name__ == "__main__":
    main()