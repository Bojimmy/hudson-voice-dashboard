import os
from google import genai
import toml
from dotenv import load_dotenv

load_dotenv()

def load_api_key():
    API_KEY = os.getenv("GEMINI_API_KEY")
    if not API_KEY:
        project_root = "/Users/bobdallavia/open-and-secure/gemini-voice-widget-2"
        secrets_path = os.path.join(project_root, ".streamlit", "secrets.toml")
        if os.path.exists(secrets_path):
            secrets = toml.load(secrets_path)
            API_KEY = secrets.get("GEMINI_API_KEY")
    return API_KEY

def debug_imagen():
    api_key = load_api_key()
    print(f"API KEY: {api_key[:5]}...{api_key[-5:] if api_key else 'None'}")
    client = genai.Client(api_key=api_key)
    
    prompt = "Indiana Jones"
    print(f"Testing with prompt: {prompt}")
    
    try:
        response = client.models.generate_images(
            model='imagen-4.0-generate-001',
            prompt=prompt,
        )
        
        print(f"Response Object: {response}")
        if response.generated_images:
            for i, img in enumerate(response.generated_images):
                print(f"--- IMAGE {i+1} ---")
                print(f"Object: {img}")
                print(f"Has 'image': {hasattr(img, 'image')}")
                if hasattr(img, 'image') and img.image:
                    print(f"Has 'image_bytes': {hasattr(img.image, 'image_bytes')}")
                    if hasattr(img.image, 'image_bytes') and img.image.image_bytes:
                        print(f"Bytes Len: {len(img.image.image_bytes)}")
                print(f"RAI Reason: {getattr(img, 'rai_filtered_reason', 'N/A')}")
        else:
            print("No generated_images in response.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_imagen()
