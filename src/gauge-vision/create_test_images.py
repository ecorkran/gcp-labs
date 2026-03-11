"""
Create simple test images that simulate gauge camera output.
In production, these would be actual field photos.
For the lab, we use solid colored images with text overlays
to represent different river conditions.
"""
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Pillow not installed. Creating minimal PNG files instead.")

import struct
import zlib
import os

OUTPUT_DIR = os.path.expanduser("~/riverpulse/gauge-vision/sample-images")

# Conditions we want to classify
CONDITIONS = {
    "clear_flow": (34, 139, 230),      # Blue — clear water
    "high_water": (139, 90, 43),        # Brown — muddy flood
    "ice_formation": (200, 220, 235),   # Light blue/white — ice
    "debris_field": (80, 80, 60),       # Dark — debris
}


def create_minimal_png(filepath, r, g, b, width=640, height=480):
    """Create a minimal valid PNG without PIL."""
    # PNG header
    header = b'\x89PNG\r\n\x1a\n'
    
    # IHDR chunk
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data)
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc & 0xffffffff)
    
    # IDAT chunk — create raw image data
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00'  # filter byte
        for x in range(width):
            raw_data += bytes([r, g, b])
    
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b'IDAT' + compressed)
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc & 0xffffffff)
    
    # IEND chunk
    iend_crc = zlib.crc32(b'IEND')
    iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc & 0xffffffff)
    
    with open(filepath, 'wb') as f:
        f.write(header + ihdr + idat + iend)


def create_test_images():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    for name, (r, g, b) in CONDITIONS.items():
        filepath = os.path.join(OUTPUT_DIR, f"gauge-001-{name}.png")
        
        if HAS_PIL:
            img = Image.new('RGB', (640, 480), (r, g, b))
            draw = ImageDraw.Draw(img)
            # Add text overlay
            draw.text((20, 20), f"Gauge: gauge-001", fill=(255, 255, 255))
            draw.text((20, 50), f"Condition: {name}", fill=(255, 255, 255))
            draw.text((20, 80), f"Simulated gauge camera image", fill=(200, 200, 200))
            img.save(filepath)
        else:
            create_minimal_png(filepath, r, g, b)
        
        print(f"Created: {filepath}")


if __name__ == "__main__":
    create_test_images()
