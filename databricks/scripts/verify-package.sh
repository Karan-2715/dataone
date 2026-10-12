#!/bin/bash
# Verify Phase 2 implementation is complete

echo "Verifying Phase 2 Databricks Lakehouse App Package..."
echo ""

MISSING=0

check_file() {
    if [ -f "$1" ]; then
        echo "✓ $1"
    else
        echo "✗ $1 (MISSING)"
        ((MISSING++))
    fi
}

check_dir() {
    if [ -d "$1" ]; then
        echo "✓ $1/"
    else
        echo "✗ $1/ (MISSING)"
        ((MISSING++))
    fi
}

echo "=== Core Files ==="
check_file "databricks/app.yml"
check_file "databricks/backend.Dockerfile"
check_file "databricks/frontend.Dockerfile"
echo ""

echo "=== Backend Implementation ==="
check_file "backend/app/services/databricks_auth_service.py"
check_file "backend/app/api/routes/databricks_auth.py"
check_file "backend/app/services/databricks_unity_catalog_service.py"
echo ""

echo "=== Scripts ==="
check_file "databricks/scripts/build-images.sh"
check_file "databricks/scripts/deploy.sh"
check_file "databricks/scripts/test-deployment.sh"
echo ""

echo "=== Marketplace Files ==="
check_file "marketplace/listing.json"
check_file "marketplace/INSTALLATION.md"
check_file "marketplace/SECURITY.md"
check_dir "marketplace/screenshots"
echo ""

echo "=== Configuration ==="
check_file ".env.example"
echo ""

echo "================================"
if [ $MISSING -eq 0 ]; then
    echo "✅ All files present - Package is complete!"
    exit 0
else
    echo "❌ $MISSING files missing - Package is incomplete"
    exit 1
fi
