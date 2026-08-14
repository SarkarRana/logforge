#!/usr/bin/env python3
"""
Flask web application example demonstrating LogCore integration.

This example shows how to integrate LogCore with a Flask web application
including request logging, correlation IDs, error handling, and structured logging.

It wires correlation IDs by hand to show the mechanics. Since v0.1.7 you can
skip all of that with the shipped middleware -- see integration_example.py:

    from logcore import WSGICorrelationIdMiddleware
    app.wsgi_app = WSGICorrelationIdMiddleware(app.wsgi_app, logger=log)

Run with: python examples/flask_example.py
"""

import time
import uuid
from functools import wraps

try:
    from flask import Flask, g, jsonify, request
except ImportError:
    print("Flask not installed. Install with: pip install flask")
    exit(1)

from logcore import get_logger

# Initialize Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key"

# Initialize logger
log = get_logger("flask_app", level="INFO", json=True)


def with_correlation_id(f):
    """Decorator to ensure correlation ID is set for request."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get or generate correlation ID
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        g.correlation_id = correlation_id

        # Execute request within correlation ID context
        with log.with_correlation_id(correlation_id):
            return f(*args, **kwargs)

    return decorated_function


@app.before_request
def before_request():
    """Log request start and set up timing."""
    g.start_time = time.time()
    correlation_id = getattr(g, "correlation_id", str(uuid.uuid4()))

    with log.with_correlation_id(correlation_id):
        log.info(
            "Request started",
            method=request.method,
            path=request.path,
            remote_addr=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "Unknown"),
        )


@app.after_request
def after_request(response):
    """Log request completion."""
    duration = (time.time() - getattr(g, "start_time", time.time())) * 1000
    correlation_id = getattr(g, "correlation_id", "unknown")

    with log.with_correlation_id(correlation_id):
        log.info(
            "Request completed",
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            duration_ms=round(duration, 2),
            content_length=response.content_length,
        )

    # Add correlation ID to response headers
    response.headers["X-Correlation-ID"] = correlation_id
    return response


@app.errorhandler(Exception)
def handle_exception(error):
    """Global exception handler with logging."""
    correlation_id = getattr(g, "correlation_id", "unknown")

    with log.with_correlation_id(correlation_id):
        log.exception(
            "Unhandled exception",
            error_type=type(error).__name__,
            method=request.method,
            path=request.path,
        )

    return (
        jsonify({"error": "Internal server error", "correlation_id": correlation_id}),
        500,
    )


# Routes
@app.route("/")
@with_correlation_id
def index():
    """Home page."""
    log.info("Home page accessed")
    return jsonify(
        {
            "message": "Welcome to LogCore Flask Example",
            "correlation_id": g.correlation_id,
        }
    )


@app.route("/users", methods=["GET"])
@with_correlation_id
def get_users():
    """Get all users."""
    log.info("Fetching all users")

    # Simulate database query with timing
    with log.time("database_query", table="users", operation="SELECT"):
        time.sleep(0.1)  # Simulate DB query
        users = [
            {"id": 1, "name": "Alice", "role": "admin"},
            {"id": 2, "name": "Bob", "role": "user"},
        ]

    log.info("Users fetched successfully", count=len(users))
    return jsonify({"users": users})


@app.route("/users/<int:user_id>", methods=["GET"])
@with_correlation_id
def get_user(user_id):
    """Get specific user."""
    log.info("Fetching user", user_id=user_id)

    # Simulate user lookup
    with log.time("user_lookup", user_id=user_id):
        time.sleep(0.05)  # Simulate lookup

        if user_id == 999:
            log.warning("User not found", user_id=user_id)
            return jsonify({"error": "User not found"}), 404

        user = {"id": user_id, "name": f"User {user_id}", "role": "user"}

    log.info("User fetched successfully", user_id=user_id, user_name=user["name"])
    return jsonify({"user": user})


@app.route("/users", methods=["POST"])
@with_correlation_id
def create_user():
    """Create a new user."""
    data = request.get_json()

    if not data:
        log.warning("Invalid request: no JSON data provided")
        return jsonify({"error": "No data provided"}), 400

    # Log the creation attempt (with sensitive data redaction)
    log.info(
        "Creating new user",
        username=data.get("username"),
        email=data.get("email"),
        password=data.get("password"),
    )  # Will be redacted if configured

    # Simulate validation
    required_fields = ["username", "email"]
    missing_fields = [field for field in required_fields if not data.get(field)]

    if missing_fields:
        log.warning(
            "Validation failed: missing required fields", missing_fields=missing_fields
        )
        return (
            jsonify(
                {"error": "Missing required fields", "missing_fields": missing_fields}
            ),
            400,
        )

    # Simulate user creation
    with log.time("user_creation", username=data["username"]):
        time.sleep(0.2)  # Simulate database insert
        user_id = 123  # Simulated new user ID

    log.info(
        "User created successfully",
        user_id=user_id,
        username=data["username"],
        email=data["email"],
    )

    return (
        jsonify(
            {
                "message": "User created successfully",
                "user_id": user_id,
                "correlation_id": g.correlation_id,
            }
        ),
        201,
    )


@app.route("/simulate-error")
@with_correlation_id
def simulate_error():
    """Simulate an error for testing error handling."""
    log.info("Simulating error for testing")

    # This will trigger the global exception handler
    raise ValueError("This is a simulated error for testing")


@app.route("/slow-operation")
@with_correlation_id
def slow_operation():
    """Simulate a slow operation with detailed timing."""
    log.info("Starting slow operation")

    with log.time("slow_operation", level="INFO"):
        # Simulate multiple phases
        with log.time("phase_1", phase="initialization"):
            time.sleep(0.3)
            log.info("Phase 1 completed")

        with log.time("phase_2", phase="processing"):
            time.sleep(0.5)
            log.info("Phase 2 completed")

        with log.time("phase_3", phase="finalization"):
            time.sleep(0.2)
            log.info("Phase 3 completed")

    log.info("Slow operation completed successfully")
    return jsonify(
        {"message": "Slow operation completed", "correlation_id": g.correlation_id}
    )


@app.route("/health")
def health_check():
    """Health check endpoint (no logging to avoid noise)."""
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    log.info("Starting Flask application", port=5000, debug=True)

    print("\nFlask LogCore Example")
    print("=" * 30)
    print("Server starting on http://localhost:5000")
    print("\nTry these endpoints:")
    print("  GET  /                    - Home page")
    print("  GET  /users              - Get all users")
    print("  GET  /users/123          - Get specific user")
    print("  GET  /users/999          - Trigger 404 error")
    print("  POST /users              - Create user (send JSON)")
    print("  GET  /simulate-error     - Trigger error")
    print("  GET  /slow-operation     - Slow operation with timing")
    print("  GET  /health             - Health check")
    print("\nExample curl commands:")
    print("  curl http://localhost:5000/")
    print("  curl http://localhost:5000/users")
    print("  curl -X POST http://localhost:5000/users \\")
    print("       -H 'Content-Type: application/json' \\")
    print(
        '       -d \'{"username":"alice","email":"alice@example.com","password":"secret123"}\''  # noqa: E501
    )
    print("  curl -H 'X-Correlation-ID: custom-123' http://localhost:5000/users")

    app.run(debug=True, port=5000)
