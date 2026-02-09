import os
import sys
import argparse
import base64
import requests
import json
import subprocess
import io

# --- CONFIGURATION ---
# 1. Project ID: Tries to grab it from your gcloud config
try:
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or subprocess.check_output(
        "gcloud config get-value project", shell=True, text=True).strip()
    # FALLBACK: Hardcode your project ID here if the above fails
    # PROJECT_ID = "your-actual-project-id" 
    
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
except Exception as e:
    print(f"❌ Setup Error: Could not determine Project ID. {e}")
    sys.exit(1)

def get_access_token():
    """Fetches the active gcloud credentials token."""
    try:
        token = subprocess.check_output("gcloud auth print-access-token", shell=True, text=True).strip()
        return token
    except Exception as e:
        print(f"❌ Auth Error: Could not run 'gcloud auth print-access-token'. Run 'gcloud auth login' in terminal.")
        return None

def upscale_image(input_file, prompt="A high quality upscaled image", factor="x4"):
    """
    Calls the Vertex AI 'imagen-4.0-upscale-preview' model.
    factor: "x2" or "x4"
    """
    if not os.path.exists(input_file):
        return f"Error: Input file {input_file} not found."

    # Normalize factor
    factor = factor.lower()
    if factor not in ["x2", "x4"]:
        factor = "x4"

    # Force a new filename to prove it worked
    suffix = "2k" if factor == "x2" else "4k"
    base, ext = os.path.splitext(input_file)
    output_file = f"{base}_{suffix}{ext}"
    
    token = get_access_token()
    if not token:
        return "Error: Authentication failed. See logs."

    print(f"🍌 Nano Upscale: Boosting {input_file} to {suffix.upper()} (Target: {output_file})...")

    # 1. Check Dimensions and Resize if needed (Vertex AI 4x limit is 17M total pixels)
    from PIL import Image
    with Image.open(input_file) as img:
        width, height = img.size
        # factor_int (2 or 4)
        f_int = int(factor.replace("x", ""))
        total_pixels_after_upscale = (width * f_int) * (height * f_int)
        
        limit = 17000000
        if total_pixels_after_upscale > limit:
            print(f"⚠️ Image too large for {factor} upscale ({total_pixels_after_upscale} px). Scaling down slightly...")
            import math
            scale = math.sqrt(limit / total_pixels_after_upscale) * 0.99 # Safety margin
            new_width = int(width * scale)
            new_height = int(height * scale)
            
            # Resize image in memory
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            image_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        else:
            # Encode Original
            with open(input_file, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

    # 2. Build Request (Explicitly hitting Imagen 4.0 Upscale)
    url = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/imagen-4.0-upscale-preview:predict"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    
    payload = {
        "instances": [
            {
                "prompt": prompt,
                "image": {"bytesBase64Encoded": image_b64}
            }
        ],
        "parameters": {
            "mode": "upscale",
            "upscaleConfig": {"upscaleFactor": factor} 
        }
    }

    # 3. Fire Request & DEBUG Response
    try:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            error_msg = f"API Error {response.status_code}: {response.text}"
            print(f"❌ {error_msg}")
            import json
            try:
                err_json = response.json()
                msg = err_json.get("error", {}).get("message", response.text)
                return f"Upscale Error: {msg}"
            except:
                return f"Upscale Error {response.status_code}: {response.text[:200]}"
            
        predictions = response.json().get("predictions", [])
        if predictions:
            upscaled_b64 = predictions[0].get("bytesBase64Encoded")
            with open(output_file, "wb") as f:
                f.write(base64.b64decode(upscaled_b64))
            
            # Verify Size
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"✅ Upscale Complete: {output_file} ({size_mb:.2f} MB)")
            return f"Success: Created {output_file} ({size_mb:.2f} MB)"
        else:
            return "Error: No image data returned from API."

    except Exception as e:
        return f"Script Error: {e}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="Input image filename")
    args = parser.parse_args()
    print(upscale_image(args.image))
