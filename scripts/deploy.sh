#!/usr/bin/env bash
set -euo pipefail

# Build Lambda deployment packages locally.
# Usage: ./scripts/deploy.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Cleaning previous builds"
rm -rf "$PROJECT_DIR/dist"
mkdir -p "$PROJECT_DIR/dist/router-pkg"
mkdir -p "$PROJECT_DIR/dist/stripe-pkg"
mkdir -p "$PROJECT_DIR/dist/shopify-pkg"
mkdir -p "$PROJECT_DIR/dist/github-pkg"
mkdir -p "$PROJECT_DIR/dist/generic-pkg"

echo "==> Installing dependencies for all packages"
for pkg_dir in router stripe shopify github generic; do
  pip install -r "$PROJECT_DIR/requirements.txt" -t "$PROJECT_DIR/dist/${pkg_dir}-pkg/" --quiet
done

echo "==> Copying router handler"
cp "$PROJECT_DIR/src/router.py" "$PROJECT_DIR/dist/router-pkg/"

echo "==> Copying processor handlers"
cp "$PROJECT_DIR/src/processors/stripe.py" "$PROJECT_DIR/dist/stripe-pkg/"
cp "$PROJECT_DIR/src/processors/shopify.py" "$PROJECT_DIR/dist/shopify-pkg/"
cp "$PROJECT_DIR/src/processors/github.py" "$PROJECT_DIR/dist/github-pkg/"
cp "$PROJECT_DIR/src/processors/generic.py" "$PROJECT_DIR/dist/generic-pkg/"

echo "==> Creating deployment zips"
cd "$PROJECT_DIR/dist/router-pkg"
zip -r "$PROJECT_DIR/dist/router.zip" . -q

cd "$PROJECT_DIR/dist/stripe-pkg"
zip -r "$PROJECT_DIR/dist/stripe.zip" . -q

cd "$PROJECT_DIR/dist/shopify-pkg"
zip -r "$PROJECT_DIR/dist/shopify.zip" . -q

cd "$PROJECT_DIR/dist/github-pkg"
zip -r "$PROJECT_DIR/dist/github.zip" . -q

cd "$PROJECT_DIR/dist/generic-pkg"
zip -r "$PROJECT_DIR/dist/generic.zip" . -q

ROUTER_SIZE=$(du -h "$PROJECT_DIR/dist/router.zip" | cut -f1)
STRIPE_SIZE=$(du -h "$PROJECT_DIR/dist/stripe.zip" | cut -f1)
SHOPIFY_SIZE=$(du -h "$PROJECT_DIR/dist/shopify.zip" | cut -f1)
GITHUB_SIZE=$(du -h "$PROJECT_DIR/dist/github.zip" | cut -f1)
GENERIC_SIZE=$(du -h "$PROJECT_DIR/dist/generic.zip" | cut -f1)

echo "==> Done:"
echo "    dist/router.zip ($ROUTER_SIZE)"
echo "    dist/stripe.zip ($STRIPE_SIZE)"
echo "    dist/shopify.zip ($SHOPIFY_SIZE)"
echo "    dist/github.zip ($GITHUB_SIZE)"
echo "    dist/generic.zip ($GENERIC_SIZE)"
echo ""
echo "Next steps:"
echo "  cd terraform && terraform plan -out=tfplan"
echo "  terraform apply tfplan"
