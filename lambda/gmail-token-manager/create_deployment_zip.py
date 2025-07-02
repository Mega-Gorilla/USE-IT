#!/usr/bin/env python3
"""
Gmail Token Manager - Deployment Package Creator

This script creates a deployment zip file for the Gmail Token Manager Lambda function
that can be uploaded directly to AWS Lambda via the console.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def create_deployment_package():
    """Create a deployment package for the Lambda function"""
    
    print("🚀 Creating Gmail Token Manager deployment package...")
    print("=" * 60)
    
    # Get current directory
    current_dir = Path(__file__).parent
    
    # Create deployment directory
    deployment_dir = current_dir / "deployment"
    zip_file = current_dir / "gmail-token-manager-deployment.zip"
    
    # Clean up previous builds
    if deployment_dir.exists():
        print("🧹 Cleaning up previous deployment...")
        shutil.rmtree(deployment_dir)
    
    if zip_file.exists():
        zip_file.unlink()
    
    # Create deployment directory
    deployment_dir.mkdir()
    print(f"📁 Created deployment directory: {deployment_dir}")
    
    # Copy Lambda function
    lambda_source = current_dir / "lambda_function.py"
    if not lambda_source.exists():
        print("❌ Error: lambda_function.py not found!")
        return False
    
    shutil.copy2(lambda_source, deployment_dir / "lambda_function.py")
    print("📄 Copied lambda_function.py")
    
    # Install dependencies
    requirements_file = current_dir / "requirements.txt"
    if not requirements_file.exists():
        print("❌ Error: requirements.txt not found!")
        return False
    
    print("📦 Installing dependencies...")
    try:
        # Install packages to deployment directory
        subprocess.run([
            sys.executable, "-m", "pip", "install",
            "-r", str(requirements_file),
            "-t", str(deployment_dir)
        ], check=True, capture_output=True, text=True)
        print("✅ Dependencies installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing dependencies: {e}")
        print(f"Output: {e.stdout}")
        print(f"Error: {e.stderr}")
        return False
    
    # Create zip file
    print("🗜️  Creating deployment zip...")
    with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(deployment_dir):
            for file in files:
                file_path = Path(root) / file
                arc_name = file_path.relative_to(deployment_dir)
                zf.write(file_path, arc_name)
    
    # Get zip file size
    zip_size_mb = zip_file.stat().st_size / (1024 * 1024)
    
    print(f"✅ Deployment package created: {zip_file}")
    print(f"📊 Package size: {zip_size_mb:.2f} MB")
    
    # Clean up deployment directory
    shutil.rmtree(deployment_dir)
    print("🧹 Cleaned up temporary files")
    
    print("\n" + "=" * 60)
    print("🎉 Deployment package ready!")
    print("=" * 60)
    print(f"📦 File: {zip_file.name}")
    print(f"📍 Location: {zip_file.absolute()}")
    
    # Check file size limits
    if zip_size_mb > 50:
        print("⚠️  WARNING: Package is larger than 50MB - you may need to use S3 for deployment")
    elif zip_size_mb > 10:
        print("💡 INFO: Package is larger than 10MB - consider optimizing dependencies")
    else:
        print("✅ Package size is optimal for direct upload")
    
    print("\n📋 Next steps:")
    print("1. Go to AWS Lambda Console")
    print("2. Create a new function or update existing one")
    print("3. Upload the zip file")
    print("4. Set up environment variables and permissions")
    print("5. Test the function")
    
    return True

if __name__ == "__main__":
    print("Gmail Token Manager - Deployment Package Creator")
    print("This will create a zip file for manual Lambda deployment\n")
    
    # Check Python version
    if sys.version_info < (3, 8):
        print("❌ Error: Python 3.8+ is required")
        sys.exit(1)
    
    # Check if pip is available
    try:
        subprocess.run([sys.executable, "-m", "pip", "--version"], 
                      check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print("❌ Error: pip is not available")
        sys.exit(1)
    
    # Create deployment package
    success = create_deployment_package()
    
    if success:
        print("\n✅ Ready to deploy! Upload the zip file to AWS Lambda.")
    else:
        print("\n❌ Failed to create deployment package")
        sys.exit(1) 