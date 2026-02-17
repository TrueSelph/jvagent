"""Lightspeed Retrieval Interact Action for retrieving and interacting with Lightspeed products."""

from typing import List, Any
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute
import logging
logger = logging.getLogger(__name__)


class LightspeedRetrievalInteractAction(InteractAction):
    """Lightspeed Retrieval Interact Action for retrieving and interacting with Lightspeed products."""

    extract_search_keywords_prompt: str = """**Task:** Extract products, details, and implied tools from queries. Return **strict JSON list** (empty if no products).  

**Rules:**  
1. **Primary Product:** Always extract the core item (e.g., "waterproof paste" → `["waterproof paste"]`).  
2. **Details:** Add sizes/materials ONLY if specified (e.g., "1/2-inch screws" → `["screw", "1/2 inch"]`).  
3. **Implied Items:** Add tools/supplies *only* if logically required (e.g., "paint" → `["paint", "paint roller"]`).  
4. **Synonyms:** Map alternate names (e.g., "PTFE tape" → `["plumber's tape"]`).  
5. **Inferred Names:** Guess the item if described by function (e.g., "tool for opening cans" → ["can opener"]).
6. **Correct Spelling:** Correct misspellings (e.g., "waterprof paste" → `["waterproof paste"]`).
7. If a user query contains a plural form of a product (e.g., "generators"), normalize to singular (["generator"]).
8. **Noise:** Skip vague terms (e.g., "heavy-duty" ignored).  
9. **Product Codes:** Product codes are alphanumeric (e.g., ["IND0001"]). If a product code is present, return only the product code and omit other keywords.
10. **Brand-only Queries:** If the query mentions only a brand name and general product type but no specific model or product code (e.g., “Duracell batteries” → ["Duracell", "battery"], "power master batteries" → ["power master", "battery"]). 

**Output Examples:**  
- Query: *"do you sell any waterproof paste"* → `["waterproof paste"]`  
- Query: *"need 10 mm bolts for metal"* → `["bolt", "10 mm", "metal", "wrench"]`  
- Query: *"What's your hours?"* → `[]`  
- Output is a valid JSON list of keywords."""

    generate_reply_directive: str ="""You are the assistant for a hardware store. Your role is to help customers by understanding their needs and recommending the right tools, materials, and products. Analyze the customer's question and respond in a friendly, helpful, and natural way.

Your response must always be in valid JSON format, structured as follows:

    "intro": "Brief, friendly introductory message", # string
    "products": # list
        "id": "Product ID",
        "summary": "Concise description of the product, including helpful tips, usage advice, or benefits"
    "outro": "Friendly closing remark inviting further questions" # string

- Only include products directly related to the user's query. Omit unrelated products.
- Do not MAKE UP any items. ONLY USE the products listed below else let the user know you do not have the item.
- Keep summaries brief: focus on key benefits, use cases, or practical tips.
- If query matches one product exactly, return ONLY that item.
- Max 3 products. Exceptions: Only show more when user explicitly asks (e.g., "show all", "list everything", "more options").
- The `intro` should be natural and tailored to the customer's question, avoid greeting the user.
- The `outro` should encourage further interaction.
- Never include raw double-quote characters (") inside string values (these break JSON). Replace any inch-quote " with a word or hyphenated form: use inch, in, or -inch (e.g., 4-inch or 4 in). You may also escape quotes as \" but preference is to avoid " entirely.

List of products available at the store:

{products}

Return a json object containing intro, products, and outro.
"""
    no_product_directive: str ="If FAQ, answer it; if product inquiry, state 'No product found' concisely."


    anchors: List[str] = attribute(
        default=[
            "The user is asking for a product",
            "The user is providing a product description, code, query, or product name",
            "The user is providing a follow-up question about a product",
            "The user is requesting details for a product",
            "The user is trying to look up an item"
        ],
        description="Anchor statements for InteractRouter routing"
    )


    async def execute(self, visitor: InteractWalker) -> None:
        """Execute Lightspeed retrieval and interaction process.
        
        Retrieves the user's product and provides it to the user.
        
        Args:
            visitor: InteractWalker instance containing interaction context
        """
        try:
            # 1. Extract Keywords
            keywords = await self.extract_search_keywords(visitor)
            if not keywords:
                await visitor.add_directive(self.no_product_directive)
                return

            # 2. Search Products
            lightspeed_api = await self.get_action("LightspeedAPIAction")
            if not lightspeed_api:
                logger.error("LightspeedAPIAction not found")
                await visitor.add_directive(self.no_product_directive)
                return

            products = []
            product_ids = set()
            for keyword in keywords:
                found_products = await lightspeed_api.search_products(keyword)
                for item in found_products:
                    if item['id'] not in product_ids:
                        product_ids.add(item['id'])
                        products.append(item)

            if not products:
                await visitor.add_directive(self.no_product_directive)
                return

            # 3. Refine Top Products
            products = self.refine_top_products(products, keywords, limit=3)
            if not products:
                await visitor.add_directive(self.no_product_directive)
                return

            # 4. Build Products Text for LLM
            products_text = await self.build_products_text(products, lightspeed_api)
            
            # 5. Generate JSON Reply
            reply = await self.generate_reply(visitor, products_text)
            if not reply or not reply.get("products"):
                await visitor.add_directive(self.no_product_directive)
                return

            # 6. Merge full product details into the reply
            keyed_products = {p['id']: p for p in products}
            for item in reply["products"]:
                p_id = str(item.get("id"))
                if p_id in keyed_products:
                    item.update(keyed_products[p_id])

            # 7. Build and Publish Reply
            if visitor.channel == 'whatsapp':
                message = self.build_whatsapp_reply(reply)
            else:
                message = self.build_web_reply(reply)

            await self.publish(visitor, message)

        except Exception as e:
            logger.error(f"Error executing LightspeedRetrievalInteractAction: {e}", exc_info=True)
            await visitor.add_directive(self.no_product_directive)

    async def extract_search_keywords(self, visitor: InteractWalker) -> list:
        """Extract search keywords from user utterance using LLM."""
        lm = await self.get_action("LanguageModelAction")
        if not lm:
            return []

        result = await lm.query_sync(
            prompt=visitor.utterance,
            system=self.extract_search_keywords_prompt
        )
        response_text = await result.get_response()
        
        try:
            import json
            import re
            # Extract JSON from potential code blocks
            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if json_match:
                keywords = json.loads(json_match.group())
            else:
                keywords = json.loads(response_text)
                
            if isinstance(keywords, list):
                new_keywords = []
                for item in keywords:
                    if " " in item:
                        new_keywords.extend(item.split(' '))
                        new_keywords.append(item.replace(' ', '-'))
                    elif "-" in item:
                        new_keywords.extend(item.split('-'))
                new_keywords.extend(keywords)
                return list(set(new_keywords))
        except Exception:
            logger.warning(f"Failed to parse keywords JSON: {response_text}")
        
        return []

    async def build_products_text(self, products: list, api: Any) -> str:
        """Fetch full descriptions and format product text for LLM."""
        output = []
        for product in products:
            content = await api.get_product_description(product.get("url"))
            p_text = f"ID: {product['id']}\n"
            if product.get('brand'):
                p_text += f"BRAND: {product['brand']}\n"
            p_text += f"TITLE: {product['title']}\n"
            p_text += f"SUMMARY: {product.get('description', '')}\n"
            p_text += f"DESCRIPTION: {content}\n"
            p_text += f"PRICE: {product['price']}\n"
            p_text += "PLEASE NOTE: All prices are VAT exclusive.\n"
            output.append(p_text)
        return "\n---\n".join(output)

    async def generate_reply(self, visitor: InteractWalker, products_text: str) -> dict:
        """Generate a structured JSON reply using LLM."""
        lm = await self.get_action("LanguageModelAction")
        if not lm:
            return {}

        prompt = f"User: {visitor.utterance}\n\nAvailable Products:\n{products_text}"
        result = await lm.query_sync(
            prompt=prompt,
            system=self.generate_reply_directive.format(products=products_text)
        )
        response_text = await result.get_response()

        try:
            import json
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(response_text)
        except Exception:
            logger.warning(f"Failed to parse reply JSON: {response_text}")
        return {}

    def refine_top_products(self, products: list, keywords: list, limit: int = 3) -> list:
        """Score and filter products based on keyword matches."""
        keywords_lower = [k.lower() for k in keywords]
        scored_products = []
        
        for product in products:
            title = (product.get('title') or "").lower()
            description = (product.get('description') or "").lower()
            brand = (product.get('brand') or "").lower()

            score = 0
            for keyword in keywords_lower:
                if keyword in title or keyword in description or keyword in brand:
                    score += 1
            
            if score > 0:
                product['score'] = score
                scored_products.append(product)

        scored_products.sort(key=lambda x: x.get('score', 0), reverse=True)
        return scored_products[:limit]

    def build_web_reply(self, reply: dict) -> str:
        """Build markdown reply for web channel."""
        message = f"{reply.get('intro', '')}\n\n"
        for product in reply.get('products', []):
            message += f"**{product.get('title', '')}:** {product.get('summary', '')}\n\n"
            message += f"Price: **${product.get('price', '')}** (VAT Exclusive)\n\n"
            if product.get('url'):
                message += f"[View Details]({product['url']})\n\n"
            if product.get('image_url'):
                message += f"![image]({product['image_url']})\n\n"
        message += f"\n{reply.get('outro', '')}"
        return message.strip()

    def build_whatsapp_reply(self, reply: dict) -> str:
        """Build simple text reply for WhatsApp (multi-message logic commented out as per plan)."""
        # Note: If jvagent/whatsapp_action supports complex layouts, this can be enhanced.
        # For now, keeping it simple as a single formatted message.
        message = f"*{reply.get('intro', '')}*\n\n"
        for product in reply.get('products', []):
            message += f"* {product.get('title', '')}\n"
            message += f"_{product.get('summary', '')}_\n"
            message += f"Price: *${product.get('price', '')}* (VAT Exclusive)\n"
            if product.get('url'):
                message += f"Details: {product['url']}\n"
            message += "\n"
        message += f"\n{reply.get('outro', '')}"
        return message.strip()
