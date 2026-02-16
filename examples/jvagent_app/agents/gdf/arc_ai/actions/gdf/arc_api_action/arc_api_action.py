"""Arc API Action

Houses Arc API credentials and endpoints.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Union

import aiohttp
import asyncio
from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)


class ArcAPIAction(Action):
    """Arc API Action for interacting with the Arc APIs."""

    endpoint: str = attribute(default="https://example.com/api/v1", description="API Endpoint")
    api_key: str = attribute(default="123", description="API Key")

    timeout: int = attribute(default=30, description="Operation timeout in seconds", ge=1)
    retries: int = attribute(default=3, description="Number of retry attempts", ge=0, le=10)

    _session: Optional[aiohttp.ClientSession] = None

    async def on_enable(self) -> None:
        """Initialize the persistent HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout
            )
        await super().on_enable()

    async def on_disable(self) -> None:
        """Close the persistent HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        await super().on_disable()

    def get_agent_identifier(self) -> str:
        """Get agent identifier, lazily fetching if needed."""
        if not self.agent_identifier:
            agent = self.get_agent()
            if agent:
                self.agent_identifier = agent.id if hasattr(agent, 'id') else agent.get('id', '')
        return self.agent_identifier

    def _get_headers(self) -> Dict[str, str]:
        """Get common headers for API requests."""
        
        return {"x-api-key": self.api_key,"Content-Type": "application/json"}

    async def http_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        data: Optional[Union[Dict[str, Any], str, bytes]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Perform HTTP request to the Resolv API with automatic auth headers and retries.
        """
        method = method.upper()
        if method not in ('GET', 'POST', 'PUT', 'DELETE'):
            raise ValueError(f"Method {method} not supported")

        effective_timeout = timeout if timeout is not None else self.timeout
        effective_retries = retries if retries is not None else self.retries

        # Determine if we can use the persistent session
        use_persistent = self._session is not None and not self._session.closed
        session = self._session if use_persistent else None
        
        # If no persistent session, create a temporary one for this request
        if not use_persistent:
            timeout_obj = aiohttp.ClientTimeout(total=effective_timeout)
            session = aiohttp.ClientSession(
                timeout=timeout_obj
            )


        last_exception = None
        timeout_obj = aiohttp.ClientTimeout(total=effective_timeout)

        # Determine effective headers for this specific request
        # If headers are provided, use them (caller responsible for full set/auth)
        # Otherwise, use defaults.
        request_headers = headers if headers is not None else self._get_headers()

        try:
            logger.warning(f"request_headers = {request_headers}")
            logger.warning(f"url = {url}")
            logger.warning(f"params = {params}")
            logger.warning(f"json_data = {json_data}")
            logger.warning(f"data = {data}")
            logger.warning(f"timeout = {timeout}")
            logger.warning(f"retries = {retries}")
            logger.warning(f"kwargs = {kwargs}")
            
            for attempt in range(effective_retries + 1):
                try:
                    
                    async with session.request(
                        method,
                        url,
                        params=params,
                        json=json_data,
                        data=data,
                        headers=request_headers,
                        timeout=timeout_obj,
                        **kwargs
                    ) as response:
                        status = response.status
                        text = await response.text()
                        try:
                            json_res = await response.json()
                        except (aiohttp.ContentTypeError, json.JSONDecodeError):
                            json_res = None
                        
                        return {'status': status, 'json': json_res, 'text': text}

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exception = e
                    if attempt == effective_retries:
                        logger.error(f"Request failed after {attempt + 1} attempts: {method} {url} - {e}")
                        break

                    wait_time = 0.5 * (2 ** attempt)
                    await asyncio.sleep(wait_time)
        finally:
            if not use_persistent and session:
                await session.close()

        if last_exception:
            raise Exception(f"Request failed: {url}") from last_exception
        raise Exception(f"Request failed: {url} (Unknown error)")

    # Contact Groups API


    async def retrieve_rank_info(self, session_id: str, pin: str="") -> dict:
        # retrieve rank info
        response = await self.http_request("POST", self.endpoint+"/ident-code/retrieve", json_data={"pin": pin, "phone": session_id})
        if(response.get("status") == 200):
            return response.get("json")
        return {}
    

    async def verify_pin(self, pin: str, session_id: str) -> dict:
        response = await self.http_request("POST", self.endpoint+"/pin/verify", json_data={"pin": pin, "phone": session_id})
        if(response.get("status") in [200]):
            return response.get("json")
        return {}
    

    async def verify_phone(self, phone: str) -> dict:
        response = await self.http_request("POST", self.endpoint+"/phone/verify", json_data={"phone": phone})
        if(response.get("status") == 200):
            return response.get("json")
        return {}
    

    async def set_security_question(self, phone: str, question_id: int, answer: str) -> dict:
        response = await self.http_request("POST", self.endpoint+"/security-questions/set", json_data={"phone": phone, "question_id": question_id, "answer": answer})
        if(response.get("status") in [204]):
            return {"success": True}
        return {}
    

    async def get_security_questions(self) -> list:
        response = await self.http_request("GET", self.endpoint+"/security-questions")
        if(response.get("status") in [200]):
            return response.get("json")
        return []
    

    async def set_pin(self, phone: str, question_id: int, answer: str, pin: str) -> dict:
        response = await self.http_request("POST", self.endpoint+"/pin/reset", json_data={"phone": phone, "pin": pin, "questions": [{"question_id": question_id, "answer": answer}]})
        if(response.get("status") in [204]):
            return {"success": True}
        return {}
    

    # polls 
    async def get_polls(self, session_id: str) -> dict:
        url = self.endpoint+"/polls?phone={phone}".format(phone=session_id)
        response = await self.http_request("GET", url)
        return response.get("json")
    

    async def update_poll(self, session_id: str, selected_option_id: int, poll_id: str) -> dict:
        payload = json.dumps({
            "phone": session_id,
            "option_id": int(selected_option_id)
        })

        url = self.endpoint+"/polls/{id}/answer".format(id=poll_id)
        response = await self.http_request("POST", url, data=payload)
        return response.get("json")
    


    # course 
    async def get_available_courses(self, session_id: str) -> list:
        url = self.endpoint+"/courses/available?phone={rank_phone}".format(rank_phone=session_id)
        response = await self.http_request("GET", url)
        if(response.get("status") in [200]):
            return response.get("json")
        return {}
    

    async def get_enrolled_courses(self, session_id: str) -> list:
        url = self.endpoint+"/courses/enrolled?phone={rank_phone}".format(rank_phone=session_id)
        response = await self.http_request("GET", url)
        if(response.get("status") in [200]):
            return response.get("json")
        return {}
    

    async def get_completed_courses(self, session_id: str) -> list:
        url = self.endpoint+"/courses/completed?phone={rank_phone}".format(rank_phone=session_id)
        response = await self.http_request("GET", url)
        if(response.get("status") in [200]):
            return response.get("json")
        return {}
    


    # categories
    async def get_subscription_categories(self) -> list:
        response = await self.http_request("GET", self.endpoint+"/categories")
        if(response.get("status") in [200]):
            return response.get("json")
        return {}
    

    async def subscribe_rank(self, session_id: str, category_ids: list) -> bool:
        response = await self.http_request("PATCH", self.endpoint+"/feeds/categories", json_data={"category_ids": category_ids, "phone": session_id})
        if(response.get("status") in [204]):
            return True
        return False

    # short pass
    async def create_short_pass(self, rank_number: str, rank_name: str, rank_phone_number: str, pass_type: str, start_date: str, end_date: str, reason_for_pass: str, particulars: str, supervisor: str, supervisor_phone_number: str, applied_on: str, reviewed_on: str="", comments: str="") -> str:
        json_data = {
            "rank_number": rank_number,
            "rank_name": rank_name,
            "rank_phone_number": rank_phone_number,
            "pass_type": pass_type,
            "start_date": start_date,
            "end_date": end_date,
            "reason_for_pass": reason_for_pass,
            "particulars": particulars,
            "supervisor": supervisor,
            "supervisor_phone_number": supervisor_phone_number,
            "applied_on": applied_on,
            "reviewed_on": reviewed_on,
            "comments": comments
        }
        response = await self.http_request("POST", self.endpoint+"/short-passes", json_data=json_data)
        if (response.get("status") in [200]):
            return response.get("json")["id"]
        return ""        
    

    async def update_short_pass(self, status:int, short_pass_id:str="", rank_number:str="", rank_name:str="", rank_phone_number:str="", pass_type:str="", start_date:str="", end_date:str="", reason_for_pass:str="", particulars:str="", supervisor:str="", supervisor_phone_number:str="", applied_on:str="", reviewed_on:str="", comments:str="") -> dict:
        json_data = {
            "rank_number": rank_number,
            "rank_name": rank_name,
            "rank_phone_number": rank_phone_number,
            "pass_type": pass_type,
            "start_date": start_date,
            "end_date": end_date,
            "reason_for_pass": reason_for_pass,
            "particulars": particulars,
            "supervisor": supervisor,
            "supervisor_phone_number": supervisor_phone_number,
            "applied_on": applied_on,
            "reviewed_on": reviewed_on,
            "comments": comments,
            "status": status
        }
        # remove empty strings     
        json_data = {k: v for (k, v) in json_data.items() if v != ""}

        response = await self.http_request("PATCH", self.endpoint+"/short-passes/"+short_pass_id, json_data=json_data)
        if (response.get("status") in [200]):
            return response.get("json")
        
        return {}
    

    async def delete_short_pass(self, short_pass_id: str) -> bool:
        response = await self.http_request("DELETE", self.endpoint+"/short-passes/"+short_pass_id)
        if (response.get("status") in [204]):
            return True
        return False

    
    async def list_short_pass(self, page:int=1, per_page:int=10000) -> dict:
        response = await self.http_request("GET", self.endpoint+"/short-passes?page={page}&per_page={per_page}".format(page=page, per_page=per_page))
        if(response.get("status") in [200]):
            return response.get("json")
        return {}

    async def get_short_pass(self, short_pass_id: str) -> dict:
        short_passes = await self.list_short_pass()
        logger.warning("short_passes")
        logger.warning(short_passes)
        for short_pass in short_passes.get("data"):
            if short_pass.get("id") == short_pass_id:
                return short_pass
        return {}
        
    

    async def status_convertor(status: str) -> int:
        STATUS_PENDING = 0
        STATUS_DENIED = 1
        STATUS_APPROVED = 2

        # Create a mapping from string names to numeric values
        status_mapping = {
            "PENDING": STATUS_PENDING,
            "DENIED": STATUS_DENIED,
            "APPROVED": STATUS_APPROVED
        }

        return status_mapping.get(status, STATUS_PENDING)