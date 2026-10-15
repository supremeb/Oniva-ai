#!/bin/bash

# Oniva Quick Start Script

echo "🚀 Starting Oniva Platform..."
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Creating from .env.example..."
    cp .env.example .env
    echo "📝 Please edit .env and add your API keys before continuing."
    echo "   OPENAI_API_KEY=your-key-here"
    echo "   ANTHROPIC_API_KEY=your-key-here"
    echo ""
    read -p "Press Enter when you've added your API keys..."
fi

# Fail early when the DB role passwords are missing (postgres creates the roles on first boot)
for var in POSTGRES_PASSWORD APP_DATABASE_PASSWORD SYSTEM_DATABASE_PASSWORD REDIS_PASSWORD SECRET_KEY; do
    value=$(grep -E "^${var}=" .env | head -n1 | cut -d= -f2-)
    if [ -z "$value" ] || [[ "$value" == CHANGE_ME* ]]; then
        echo "❌ ${var} is not set in .env (generate one: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")"
        exit 1
    fi
done

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

echo "🐳 Starting Docker containers..."
docker compose up -d

echo ""
echo "✅ Oniva is starting up!"
echo ""
echo "📍 Services will be available at:"
echo "   Frontend:    http://localhost:3000"
echo "   Backend API: http://localhost:8000"
echo "   API Docs:    http://localhost:8000/docs"
echo "   Health:      http://localhost:8000/health"
echo ""
echo "📊 View logs with: docker compose logs -f"
echo "🛑 Stop with: docker compose down"
echo ""
echo "⏳ Waiting for services to be ready..."

# Wait for backend to be healthy
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Backend is ready!"
        break
    fi
    attempt=$((attempt + 1))
    sleep 2
    echo -n "."
done

if [ $attempt -eq $max_attempts ]; then
    echo ""
    echo "⚠️  Backend didn't start in time. Check logs with: docker compose logs backend"
else
    echo ""
    echo "🎉 Oniva is ready! Visit http://localhost:3000"
fi
