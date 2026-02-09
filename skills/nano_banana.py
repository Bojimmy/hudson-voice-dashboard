
import os
import sys
import argparse
from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO
import toml
from nano_upscale import upscale_image

# --- CONFIG ---
# Try environment first, then secrets.toml
API_KEY = os.getenv("GEMINI_API_KEY") 
if not API_KEY:
    try:
        # Navigate up to find .streamlit/secrets.toml
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir) # up one level from skills/
        secrets_path = os.path.join(project_root, ".streamlit", "secrets.toml")
        
        # If not found, try two levels up (if in gemini-voice-widget-2/skills)
        if not os.path.exists(secrets_path):
             secrets_path = os.path.join(os.path.dirname(project_root), ".streamlit", "secrets.toml")

        if os.path.exists(secrets_path):
            secrets = toml.load(secrets_path)
            API_KEY = secrets.get("GEMINI_API_KEY")
    except Exception as e:
        print(f"⚠️ Warning loading secrets: {e}")

def generate_image(prompt, output_filename="generated_image.png", aspect_ratio="1:1"):
    """
    Calls the Nano Banana (Gemini Image) model to generate visuals.
    Args:
        aspect_ratio (str): "1:1", "16:9", "9:16", "4:3", "3:4"
    """
    print(f"🍌 Nano Banana: Dreaming up '{prompt}' ({aspect_ratio})...")
    
    if not API_KEY:
        print("❌ Error: GEMINI_API_KEY not found in environment or secrets.")
        return "Error: API Key missing"

    client = genai.Client(api_key=API_KEY)

    try:
        # Validate Aspect Ratio
        valid_ratios = ["1:1", "16:9", "9:16", "4:3", "3:4"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1" # Fallback

        # Try generating with Imagen 4.0
        MODEL_ID = 'imagen-4.0-generate-001' 
        
        try:
            response = client.models.generate_images(
                model=MODEL_ID,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    output_mime_type="image/png",
                    include_rai_reason=True
                )
            )
        except Exception as e:
            print(f"⚠️ Imagen 4.0 Failed: {e}. Falling back to 3.0...")
            MODEL_ID = 'imagen-3.0-generate-001'
            response = client.models.generate_images(
                model=MODEL_ID,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    output_mime_type="image/png",
                    include_rai_reason=True
                )
            )
        
        # Save the result
        if response.generated_images:
            for i, generated_image in enumerate(response.generated_images):
                # Check for Safety Filter blocks
                if not generated_image.image:
                    reason = getattr(generated_image, 'rai_filtered_reason', 'Unknown Safety Filter')
                    print(f"❌ Image {i+1} was blocked by Safety Filters: {reason}")
                    return f"Error: Image blocked by Safety Filters ({reason}). Use a cleaner prompt."

                try:
                    # Open raw bytes
                    img_bytes = generated_image.image.image_bytes
                    
                    # FALLBACK: If 4.0 returns empty bytes (silent filter), try 3.0
                    if not img_bytes and MODEL_ID == 'imagen-4.0-generate-001':
                        print(f"⚠️ Imagen 4.0 returned empty bytes. Falling back to 3.0...")
                        MODEL_ID = 'imagen-3.0-generate-001'
                        response_fb = client.models.generate_images(
                            model=MODEL_ID,
                            prompt=prompt,
                            config=types.GenerateImagesConfig(
                                number_of_images=1,
                                aspect_ratio=aspect_ratio,
                                output_mime_type="image/png",
                                include_rai_reason=True
                            )
                        )
                        if response_fb.generated_images and response_fb.generated_images[0].image:
                            img_bytes = response_fb.generated_images[0].image.image_bytes
                            print(f"✅ Fallback to 3.0 Succeeded.")

                    if not img_bytes:
                         print(f"❌ Empty bytes DEBUG: {generated_image}")
                         return "Error: Image generation failed (Empty Bytes). The prompt might be triggering a sensitive content filter. Try simplifying the description."
                         
                    image = Image.open(BytesIO(img_bytes))
                    
                    # Save with maximum quality settings (optimize=False)
                    image.save(output_filename, format="PNG", optimize=False)
                    
                    # Automatically trigger 2k Upscale
                    upscale_2k = upscale_image(output_filename, prompt=prompt, factor="x2")
                    
                    # Automatically trigger 4k Upscale
                    upscale_4k = upscale_image(output_filename, prompt=prompt, factor="x4")
                    
                    size_mb = os.path.getsize(output_filename) / (1024 * 1024)
                    print(f"✅ Image Created (PRO 4.0): {output_filename} ({size_mb:.2f} MB)")
                    
                    # Combine status messages
                    status = f"2K: {upscale_2k} | 4K: {upscale_4k}"
                    return f"{status} (Original: {output_filename})"
                except Exception as img_err:
                    print(f"❌ Error processing image bytes: {img_err}")
                    # Check for RAI reasons even if image bytes aren't readable
                    rai_info = getattr(generated_image, 'rai_filtered_reason', 'None')
                    return f"Error: Could not process image. Safety Filter Info: {rai_info}"
        else:
            return "Failure: No image data returned from API. The prompt may have triggered safety filters."

    except Exception as e:
        print(f"❌ Nano Banana Error: {e}")
        return f"Error: {e}"

if __name__ == "__main__":
    # Allow CLI usage: python nano_banana.py "A cyberpunk city" --out output.png
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", help="The image description")
    parser.add_argument("--out", default="nano_banana_output.png", help="Output filename")
    parser.add_argument("--aspect", default="1:1", help="Aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4)")
    args = parser.parse_args()
    
    generate_image(args.prompt, args.out, args.aspect)

