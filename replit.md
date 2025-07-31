# Options Trading Dashboard

## Overview

This is a Flask-based web application that serves as a dashboard for automated options trading using the Alpaca API. The system processes webhook signals to execute options trades, specifically focusing on CALL and PUT options with 2-day expiration periods. It provides a web interface for monitoring trading activity, managing API credentials, and viewing order history.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Backend Framework
The application uses Flask as the web framework with SQLAlchemy for database operations. The architecture follows a traditional MVC pattern with clear separation between models, routes, and services.

### Database Design
Uses SQLAlchemy ORM with three main models:
- **TradingConfig**: Stores Alpaca API credentials and configuration
- **Order**: Tracks all trading orders with status, contract details, and execution information
- **WebhookLog**: Maintains audit logs of incoming webhook requests

The database supports both SQLite (default) and PostgreSQL through environment configuration, with connection pooling and health checks enabled.

### Trading Service Layer
The `TradingService` class handles all Alpaca API interactions including:
- API connection testing and validation
- Options contract discovery for ATM (at-the-money) strikes
- Order execution and status tracking
- 2-day expiration date calculations with business day logic

### Web Interface
Frontend uses Bootstrap with dark theme for a modern trading dashboard aesthetic. The interface provides three main views:
- **Dashboard**: Real-time overview with connection status and order statistics
- **Orders**: Paginated history of all trading orders with detailed status information
- **Settings**: Configuration panel for Alpaca API credentials

### Configuration Management
Supports dual configuration approach:
- Database-stored credentials (primary)
- Environment variable fallback for deployment flexibility

### Security Considerations
- Session management with configurable secret keys
- Proxy support for deployment behind load balancers
- Input validation and error handling throughout the application

## External Dependencies

### Trading Platform
- **Alpaca Markets API**: Primary trading platform using paper trading environment
- Uses both REST API endpoints for account management and options contract search
- Requires API key and secret key authentication

### Frontend Libraries
- **Bootstrap 5**: UI framework with dark theme support
- **Font Awesome**: Icon library for enhanced visual interface
- **Custom CSS**: Additional styling for trading-specific components

### Python Libraries
- **Flask**: Web framework and routing
- **SQLAlchemy**: Database ORM and connection management
- **Werkzeug**: WSGI utilities and middleware
- **Requests**: HTTP client for external API calls

### Database Support
- **SQLite**: Default development database
- **PostgreSQL**: Production database option via DATABASE_URL environment variable
- Connection pooling and health monitoring configured for production use