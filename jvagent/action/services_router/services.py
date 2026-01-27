"""Service definitions for ServicesRouter.

This file defines available services with their anchors for semantic routing.
Each service has a name, description, and list of anchor phrases that trigger routing to it.
"""

from typing import List, TypedDict


class ServiceDefinition(TypedDict):
    """Definition of a service for routing."""
    name: str
    description: str
    category: str
    anchors: List[str]
    connection_info: str  # URL or Action Name
    enabled: bool


# Available services with their routing anchors
SERVICES: List[ServiceDefinition] = [
    {
        "name": "TrainingSignup",
        "description": "Handles user registration and training session scheduling",
        "category": "Education",
        "connection_info": "jvagent/signup_interview_interact_action",
        "enabled": True,
        "anchors": [
            "User wants to sign up or register for jvagent training",
            "User wants to enroll or join jvagent training",
            "User wants to sign up for training",
            "User wants to register for training",
            "User asks about training registration",
            "User wants to schedule a training session",
        ],
    },
    {
        "name": "News Retrieval",
        "description": "Can access and retrieve news articles from the web",
        "category": "News",
        "connection_info": "https://news.google.com",
        "enabled": True,
        "anchors": [
            "User requests news articles",
            "User asks for news updates",
            "User wants to find news articles",
            "User asks for news articles",
            "User asks about news articles",
            "User requests news articles",
            "I want to read the news",
            "What is happening in the world?",
        ],
    },
    {
        "name": "TechStore",
        "description": "Online store for computer hardware and electronics",
        "category": "Electronics",
        "connection_info": "https://www.example-tech-store.com",
        "enabled": True,
        "anchors": [
            "User looking for computer parts",
            "User wants to buy hardware",
            "I need a new graphics card",
            "Where can I buy a laptop?",
            "Hardware store",
            "Electronics shop",
        ],
    },
    {
        "name": "GrandHotel",
        "description": "Luxury hotel booking service",
        "connection_info": "https://www.grand-hotel-example.com/book",
        "enabled": True,
        "category": "Hotels",
        "subcategory": ["luxury", "dine-in", "restaurant"],
        "tags": ["hotels", "luxury", "restaurant"],
        "address": "123 Prince St, Nezzetown, USA",
        "features": ["bookings", "reservations", "restaurant"],
    },
    {
        "name": "PizzaPalace",
        "description": "Pizza delivery and takeout",
        "connection_info": "https://www.pizza-palace-example.com",
        "enabled": True,
        "category": "food",
        "subcategory": ["fast food", "delivery"],
        "tags": ["food", "pizza", "fast food"],
        "address": "123 Prince St, Nezzetown, USA",
        "features": ["delivery", "takeout"],
    },
    {
        "name": "SushiWorld",
        "description": "Fresh sushi and japanese cuisine",
        "connection_info": "https://www.sushi-world-example.com",
        "enabled": True,
        "category": "food",
        "subcategory": ["restaurant", "dine-in", "asian cuisine"],
        "tags": ["food", "japanese cuisine", "sushi"],
        "address": "123 Main St, Anytown, USA",
        "features": ["indoor dining", "takeout"],
    },
    {
        "name": "Queen's Chicken Palace",
        "description": "Delicious food and drinks",
        "connection_info": "https://www.queen-chicken-example.com",
        "enabled": True,
        "category": "food",
        "subcategory": ["fast food", "delivery"],
        "tags": ["food", "chicken", "fast food", "burgers", "sandwiches", "pizza"],
        "address": "123 Prince St, Nezzetown, USA",
        "features": ["delivery", "takeout"],
    },
    {
        "name": "CoffeeCorner",
        "description": "Premium coffee and pastries",
        "connection_info": "https://www.coffee-corner-example.com",
        "enabled": True,
        "category": "food",
        "subcategory": ["cafe", "bakery"],
        "tags": ["baked goods", "coffee", "cafe"],
        "address": "789 Pine Rd, Metropolis, USA",
        "features": ["wifi", "outdoor seating"],
    },
    {
        "name": "GroceryStore",
        "description": "Online grocery store",
        "connection_info": "https://www.grocery-store-example.com",
        "enabled": True,
        "category": "food",
        "subcategory": ["groceries", "online shopping"],
        "tags": ["food", "groceries", "online shopping"],
        "address": "123 Main St, Anytown, USA",
        "features": ["online shopping", "delivery"],
    },
    {
        "name": "SupportTicket",
        "description": "Creates and manages support tickets for technical issues",
        "category": "Support",
        "connection_info": "jvagent/support_ticket_action",
        "enabled": True,
        "anchors": [
            "User reports a bug or issue",
            "User needs technical support",
            "User wants to create a support ticket",
            "User has a problem with the system",
            "User requests help with an error",
        ],
    },
    {
        "name": "AccountManagement",
        "description": "Handles account updates, password resets, and profile management",
        "category": "Account",
        "connection_info": "jvagent/account_action",
        "enabled": True,
        "anchors": [
            "User wants to update their account",
            "User wants to change their password",
            "User requests profile update",
            "User wants to manage account settings",
            "User asks about account details",
        ],
    },
    {
        "name": "Billing",
        "description": "Handles billing inquiries, invoices, and payment issues",
        "category": "Billing",
        "connection_info": "jvagent/billing_action",
        "enabled": True,
        "anchors": [
            "User asks about billing or invoices",
            "User wants to see payment history",
            "User has a payment issue",
            "User requests a refund",
            "User asks about pricing or costs",
        ],
    },
]


def get_enabled_services() -> List[ServiceDefinition]:
    """Get list of enabled services.

    Returns:
        List of service definitions that are enabled
    """
    return [service for service in SERVICES if service.get("enabled", True)]


def get_service_by_name(name: str) -> ServiceDefinition | None:
    """Get a service definition by name.

    Args:
        name: Service name to look up

    Returns:
        Service definition if found, None otherwise
    """
    for service in SERVICES:
        if service["name"] == name:
            return service
    return None
