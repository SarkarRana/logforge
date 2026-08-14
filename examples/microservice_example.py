#!/usr/bin/env python3
"""
Microservice example with LogCore demonstrating advanced logging patterns.

This example simulates a microservice architecture with:
- Service-to-service communication
- Distributed tracing with correlation IDs
- Structured error handling
- Performance monitoring
- Business metrics logging
"""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from logcore import get_logger

# Configure loggers for different services
user_service_log = get_logger("user-service", level="INFO", json=True)
order_service_log = get_logger("order-service", level="INFO", json=True)
payment_service_log = get_logger("payment-service", level="INFO", json=True)
notification_service_log = get_logger("notification-service", level="INFO", json=True)
api_gateway_log = get_logger("api-gateway", level="INFO", json=True)


@dataclass
class User:
    id: int
    name: str
    email: str
    tier: str  # bronze, silver, gold


@dataclass
class Order:
    id: int
    user_id: int
    items: List[Dict]
    total_amount: float
    status: str


class UserService:
    """Simulated user service."""

    def __init__(self):
        self.users = {
            1: User(1, "Alice Johnson", "alice@example.com", "gold"),
            2: User(2, "Bob Smith", "bob@example.com", "silver"),
            3: User(3, "Charlie Brown", "charlie@example.com", "bronze"),
        }

    async def get_user(self, user_id: int, correlation_id: str) -> Optional[User]:
        """Fetch user by ID."""
        with user_service_log.with_correlation_id(correlation_id):
            user_service_log.info("User lookup requested", user_id=user_id)

            async with user_service_log.time("user_lookup", user_id=user_id):
                # Simulate database lookup
                await asyncio.sleep(0.05)

                user = self.users.get(user_id)

                if user:
                    user_service_log.info(
                        "User found",
                        user_id=user_id,
                        user_name=user.name,
                        user_tier=user.tier,
                    )
                else:
                    user_service_log.warning("User not found", user_id=user_id)

                return user

    async def update_user_activity(
        self, user_id: int, activity: str, correlation_id: str
    ):
        """Update user activity log."""
        with user_service_log.with_correlation_id(correlation_id):
            user_service_log.info(
                "Updating user activity",
                user_id=user_id,
                activity=activity,
                timestamp=time.time(),
            )

            # Simulate activity update
            await asyncio.sleep(0.02)


class OrderService:
    """Simulated order service."""

    def __init__(self):
        self.orders = {}
        self.next_order_id = 1000

    async def create_order(
        self, user_id: int, items: List[Dict], correlation_id: str
    ) -> Order:
        """Create a new order."""
        with order_service_log.with_correlation_id(correlation_id):
            order_id = self.next_order_id
            self.next_order_id += 1

            total_amount = sum(item["price"] * item["quantity"] for item in items)

            order_service_log.info(
                "Creating order",
                order_id=order_id,
                user_id=user_id,
                item_count=len(items),
                total_amount=total_amount,
            )

            async with order_service_log.time("order_creation", order_id=order_id):
                # Simulate order processing
                await asyncio.sleep(0.1)

                order = Order(
                    id=order_id,
                    user_id=user_id,
                    items=items,
                    total_amount=total_amount,
                    status="pending",
                )

                self.orders[order_id] = order

                # Log business metrics
                order_service_log.info(
                    "Order created successfully",
                    order_id=order_id,
                    user_id=user_id,
                    total_amount=total_amount,
                    item_count=len(items),
                    status="pending",
                )

                return order

    async def update_order_status(
        self, order_id: int, status: str, correlation_id: str
    ):
        """Update order status."""
        with order_service_log.with_correlation_id(correlation_id):
            if order_id in self.orders:
                old_status = self.orders[order_id].status
                self.orders[order_id].status = status

                order_service_log.info(
                    "Order status updated",
                    order_id=order_id,
                    old_status=old_status,
                    new_status=status,
                )
            else:
                order_service_log.error(
                    "Cannot update order: not found", order_id=order_id
                )


class PaymentService:
    """Simulated payment service."""

    async def process_payment(self, order: Order, correlation_id: str) -> bool:
        """Process payment for an order."""
        with payment_service_log.with_correlation_id(correlation_id):
            payment_service_log.info(
                "Processing payment",
                order_id=order.id,
                amount=order.total_amount,
                user_id=order.user_id,
            )

            async with payment_service_log.time(
                "payment_processing", order_id=order.id, amount=order.total_amount
            ):
                # Simulate payment processing
                await asyncio.sleep(0.3)

                # Simulate payment failure for demonstration (10% chance)
                success = random.random() > 0.1

                if success:
                    payment_service_log.info(
                        "Payment processed successfully",
                        order_id=order.id,
                        amount=order.total_amount,
                        transaction_id=f"txn_{order.id}_{int(time.time())}",
                    )
                else:
                    payment_service_log.error(
                        "Payment processing failed",
                        order_id=order.id,
                        amount=order.total_amount,
                        error_code="PAYMENT_DECLINED",
                        error_reason="Insufficient funds",
                    )

                return success


class NotificationService:
    """Simulated notification service."""

    async def send_order_confirmation(
        self, user: User, order: Order, correlation_id: str
    ):
        """Send order confirmation notification."""
        with notification_service_log.with_correlation_id(correlation_id):
            notification_service_log.info(
                "Sending order confirmation",
                user_id=user.id,
                user_email=user.email,
                order_id=order.id,
                notification_type="order_confirmation",
            )

            async with notification_service_log.time(
                "notification_send",
                notification_type="order_confirmation",
                user_id=user.id,
            ):
                # Simulate email sending
                await asyncio.sleep(0.05)

                notification_service_log.info(
                    "Order confirmation sent successfully",
                    user_id=user.id,
                    order_id=order.id,
                    delivery_method="email",
                )

    async def send_payment_failure_notification(
        self, user: User, order: Order, correlation_id: str
    ):
        """Send payment failure notification."""
        with notification_service_log.with_correlation_id(correlation_id):
            notification_service_log.info(
                "Sending payment failure notification",
                user_id=user.id,
                user_email=user.email,
                order_id=order.id,
                notification_type="payment_failure",
            )

            async with notification_service_log.time(
                "notification_send",
                notification_type="payment_failure",
                user_id=user.id,
            ):
                await asyncio.sleep(0.05)

                notification_service_log.info(
                    "Payment failure notification sent",
                    user_id=user.id,
                    order_id=order.id,
                    delivery_method="email",
                )


class APIGateway:
    """Simulated API Gateway orchestrating microservices."""

    def __init__(self):
        self.user_service = UserService()
        self.order_service = OrderService()
        self.payment_service = PaymentService()
        self.notification_service = NotificationService()

    async def process_order_request(
        self, user_id: int, items: List[Dict], correlation_id: str
    ) -> Dict:
        """Process a complete order request through the microservices."""
        with api_gateway_log.with_correlation_id(correlation_id):
            api_gateway_log.info(
                "Order request received",
                user_id=user_id,
                item_count=len(items),
                request_id=correlation_id,
            )

            try:
                async with api_gateway_log.time(
                    "full_order_processing", user_id=user_id
                ):
                    # Step 1: Validate user
                    user = await self.user_service.get_user(user_id, correlation_id)
                    if not user:
                        api_gateway_log.error(
                            "Order failed: user not found", user_id=user_id
                        )
                        return {"success": False, "error": "User not found"}

                    # Step 2: Create order
                    order = await self.order_service.create_order(
                        user_id, items, correlation_id
                    )

                    # Step 3: Process payment
                    payment_success = await self.payment_service.process_payment(
                        order, correlation_id
                    )

                    if payment_success:
                        # Step 4a: Update order status to completed
                        await self.order_service.update_order_status(
                            order.id, "completed", correlation_id
                        )

                        # Step 5a: Send confirmation
                        await self.notification_service.send_order_confirmation(
                            user, order, correlation_id
                        )

                        # Step 6a: Update user activity
                        await self.user_service.update_user_activity(
                            user_id, f"completed_order_{order.id}", correlation_id
                        )

                        api_gateway_log.info(
                            "Order processed successfully",
                            user_id=user_id,
                            order_id=order.id,
                            total_amount=order.total_amount,
                            user_tier=user.tier,
                        )

                        return {
                            "success": True,
                            "order_id": order.id,
                            "total_amount": order.total_amount,
                            "status": "completed",
                        }

                    else:
                        # Step 4b: Update order status to failed
                        await self.order_service.update_order_status(
                            order.id, "failed", correlation_id
                        )

                        # Step 5b: Send failure notification
                        await self.notification_service.send_payment_failure_notification(  # noqa: E501
                            user, order, correlation_id
                        )

                        api_gateway_log.error(
                            "Order failed due to payment issues",
                            user_id=user_id,
                            order_id=order.id,
                            total_amount=order.total_amount,
                        )

                        return {
                            "success": False,
                            "order_id": order.id,
                            "error": "Payment failed",
                            "status": "failed",
                        }

            except Exception as e:
                api_gateway_log.exception(
                    "Unexpected error during order processing",
                    user_id=user_id,
                    error_type=type(e).__name__,
                )

                return {"success": False, "error": "Internal server error"}


async def simulate_load_test(gateway: APIGateway, num_requests: int = 10):
    """Simulate multiple concurrent order requests."""
    api_gateway_log.info("Starting load test simulation", num_requests=num_requests)

    # Sample items for orders
    sample_items = [
        [{"name": "Widget A", "price": 29.99, "quantity": 1}],
        [{"name": "Widget B", "price": 45.00, "quantity": 2}],
        [
            {"name": "Widget C", "price": 15.99, "quantity": 3},
            {"name": "Widget D", "price": 22.50, "quantity": 1},
        ],
        [{"name": "Premium Widget", "price": 99.99, "quantity": 1}],
    ]

    # Create concurrent requests
    tasks = []
    for i in range(num_requests):
        user_id = random.randint(1, 3)  # Random user from our sample
        items = random.choice(sample_items)
        correlation_id = f"load-test-{i+1:03d}"

        task = gateway.process_order_request(user_id, items, correlation_id)
        tasks.append(task)

    # Execute all requests concurrently
    async with api_gateway_log.time("load_test_execution", num_requests=num_requests):
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Analyze results
    successful_orders = sum(
        1 for r in results if isinstance(r, dict) and r.get("success")
    )
    failed_orders = len(results) - successful_orders

    api_gateway_log.info(
        "Load test completed",
        total_requests=num_requests,
        successful_orders=successful_orders,
        failed_orders=failed_orders,
        success_rate=f"{(successful_orders/num_requests)*100:.1f}%",
    )

    return results


async def main():
    """Main example runner."""
    print("Microservice LogCore Example")
    print("=" * 40)
    print("Simulating microservice architecture with distributed logging...")
    print()

    gateway = APIGateway()

    # Example 1: Single order processing
    print("1. Processing a single order...")
    result = await gateway.process_order_request(
        user_id=1,
        items=[
            {"name": "Premium Widget", "price": 99.99, "quantity": 1},
            {"name": "Basic Widget", "price": 19.99, "quantity": 2},
        ],
        correlation_id="example-001",
    )
    print(f"Result: {json.dumps(result, indent=2)}")
    print()

    # Example 2: Load test simulation
    print("2. Running load test simulation...")
    await simulate_load_test(gateway, num_requests=15)
    print()

    # Example 3: Error scenarios
    print("3. Testing error scenarios...")

    # Invalid user
    result = await gateway.process_order_request(
        user_id=999,  # Non-existent user
        items=[{"name": "Test Widget", "price": 10.00, "quantity": 1}],
        correlation_id="error-test-001",
    )
    print(f"Invalid user result: {json.dumps(result, indent=2)}")
    print()

    print("Microservice example completed!")
    print("\nKey LogCore features demonstrated:")
    print("- ✅ Distributed correlation IDs across services")
    print("- ✅ Structured JSON logging for observability")
    print("- ✅ Performance timing and monitoring")
    print("- ✅ Error handling and exception logging")
    print("- ✅ Business metrics and event tracking")
    print("- ✅ Concurrent request handling")


if __name__ == "__main__":
    asyncio.run(main())
