"""Lightspeed API Action for interacting with the Lightspeed platform.

Provides methods for managing contacts, issues, notices, and project communications.
"""
import json
import logging

import aiohttp
import asyncio
from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)


class LightspeedAPIAction(Action):
    """Lightspeed API Action for interacting with the Lightspeed platform."""

    base_url: str = 'https://www.silviesonline.com'

    image_base_url: str = 'https://cdn.shoplightspeed.com/shops/668079/files'

    timeout: int = attribute(default=30, description="Operation timeout in seconds", ge=1)
    retries: int = attribute(default=1, description="Number of retry attempts", ge=0, le=10)

    async def search_products(self, query: str) -> list:
        query_url = f"{self.base_url}/search/{query}?format=json&limit=99"
        
        for attempt in range(self.retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(query_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as response:
                        if response.status != 200:
                            logger.warning(f"Request failed with status {response.status}")
                            logger.warning(query_url)
                            return []
                        
                        data = await response.json()
                        products = data.get("collection", {}).get("products", {})
                        
                        if products == False:
                            return []
                        
                        output = []
                        for product in products.values():
                            output.append({
                                'id': json.dumps(product.get('id')),
                                'title': json.dumps(product.get('fulltitle').rstrip('"')),
                                'description': json.dumps(product.get('description').rstrip('"')),
                                'brand': None if product.get("brand") == False else product.get("brand", {}).get("title"),
                                'price': product.get("price", {}).get("price"),
                                'url': f"{self.base_url}/" + product.get('url'),
                                'image_url': f"{self.image_base_url}/{product.get('image')}/300x300x2/image.jpg",
                            })
                        
                        return output
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Attempt {attempt + 1}/{self.retries} failed: {e}")
                if attempt == self.retries - 1:
                    return []
                await asyncio.sleep(2 ** attempt)
        return []

    async def get_product_description(self, url: str) -> str:
        for attempt in range(self.retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{url}?format=json", timeout=aiohttp.ClientTimeout(total=self.timeout)) as response:
                        data = await response.json()
                        content = data.get("product", {}).get("content")
                        return self.html_to_readable_string(content)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Attempt {attempt + 1}/{self.retries} failed: {e}")
                if attempt == self.retries - 1:
                    return ""
                await asyncio.sleep(2 ** attempt)
        return ""

    

    def html_to_readable_string(self, html_text: str):
        from bs4 import BeautifulSoup
        import re
        
        soup = BeautifulSoup(html_text, 'html.parser')
        
        for element in soup.find_all(['p', 'br', 'li', 'ul', 'ol']):
            if element.name in ['p', 'br']:
                element.insert_after('\n')
            elif element.name == 'li':
                element.insert_before('\n- ')
            elif element.name in ['ul', 'ol']:
                element.insert_before('\n')
                element.insert_after('\n')
        
        for strong in soup.find_all(['strong', 'b']):
            strong.string = f"*{strong.get_text()}*"
        
        for em in soup.find_all(['em', 'i']):
            em.string = f"_{em.get_text()}_"
        
        text = soup.get_text()
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = text.strip()
        
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&amp;', '&').replace('&quot;', '"')
        text = text.replace('&nbsp;', ' ')
        
        return text

