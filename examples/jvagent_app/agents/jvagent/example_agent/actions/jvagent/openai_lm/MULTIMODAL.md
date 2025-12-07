# Multimodal Support (Vision)

The OpenAI Model Action supports multimodal queries combining text and images using GPT-4o and GPT-4 Vision models.

## Supported Models

- `gpt-4o` - Latest multimodal model (recommended)
- `gpt-4-turbo` - GPT-4 with vision support
- `gpt-4-vision-preview` - Vision preview model

## Usage Examples

### 1. Analyze Image from URL

```python
from jvagent.action.model import OpenAILanguageModelAction

async def analyze_image(model_action_id: str, image_url: str):
    model = await OpenAILanguageModelAction.get(model_action_id)
    
    # Create content with text and image
    content = model.create_image_content(
        text="What's in this image? Provide a detailed description.",
        image_url=image_url
    )
    
    # Query with image
    result = await model.query_sync(content)
    return await result.get_response()
```

### 2. Analyze Local Image (Base64)

```python
import base64

async def analyze_local_image(model_action_id: str, image_path: str):
    model = await OpenAILanguageModelAction.get(model_action_id)
    
    # Load and encode image
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()
    
    # Create content with base64 image
    content = model.create_image_content(
        text="Analyze this image and identify any objects.",
        image_base64=image_data,
        image_detail="high"  # Optional: "low", "auto" (default), or "high"
    )
    
    result = await model.query_sync(content)
    return await result.get_response()
```

### 3. Compare Multiple Images

```python
async def compare_images(model_action_id: str, img1_url: str, img2_url: str):
    model = await OpenAILanguageModelAction.get(model_action_id)
    
    # Create content with multiple images
    content = model.create_multimodal_content(
        text="Compare these two images. What are the main differences?",
        images=[
            {"url": img1_url, "detail": "high"},
            {"url": img2_url, "detail": "high"}
        ]
    )
    
    result = await model.query_sync(content)
    return await result.get_response()
```

## Best Practices

1. **Use appropriate detail level**: `low` for basic recognition, `high` for detailed analysis
2. **Optimize image size**: Resize large images before encoding to save tokens
3. **Cache results**: Vision queries are expensive, cache common analyses
4. **Provide context**: Clear text prompts get better vision results
5. **Handle errors gracefully**: Image loading/processing can fail
