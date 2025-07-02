# Gmail Token Manager Lambda Function

A serverless AWS Lambda function for managing Gmail OAuth tokens with automatic refresh capabilities. This function stores Gmail credentials in DynamoDB, validates token expiry, and automatically refreshes tokens when needed.

## 🎯 **Features**

- **Automatic Token Refresh**: Checks if tokens are valid for more than 30 minutes, refreshes if needed
- **DynamoDB Storage**: Secure credential storage with user-based isolation
- **Easy Integration**: Simple REST-like API for token management
- **Error Handling**: Comprehensive error handling and logging
- **Scalable**: Serverless architecture that scales automatically

## 🏗️ **Architecture**

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Your App      │───▶│  Lambda Function │───▶│   DynamoDB      │
│ (browser-use)   │    │ gmail-token-mgr  │    │ gmail-creds     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                ▲
                                │
                       ┌─────────────────┐
                       │  Google OAuth   │
                       │      API        │
                       └─────────────────┘
```

## 🚀 **Quick Start**

### Prerequisites

1. **AWS CLI** installed and configured
2. **Python 3.11+** installed
3. **Gmail API credentials** from Google Cloud Console

### 1. Deploy the Lambda Function

```bash
# Clone and navigate to the lambda directory
cd lambda/gmail-token-manager

# Make the deploy script executable
chmod +x deploy.sh

# Deploy everything (DynamoDB, IAM roles, Lambda function)
./deploy.sh
```

This script will:
- ✅ Create DynamoDB table (`gmail-credentials`)
- ✅ Create IAM role with necessary permissions
- ✅ Package and deploy the Lambda function
- ✅ Set up environment variables

### 2. Set Up Google OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable Gmail API
3. Create OAuth 2.0 credentials (Desktop application)
4. Note down: `client_id`, `client_secret`
5. Complete OAuth flow to get `refresh_token`

### 3. Store Credentials

```python
from usage_example import GmailTokenClient

client = GmailTokenClient('gmail-token-manager', 'us-east-1')

# Store your OAuth credentials
client.store_credentials(
    user_id="your-unique-user-id",
    client_id="your-client-id.apps.googleusercontent.com",
    client_secret="your-client-secret",
    refresh_token="your-refresh-token-from-oauth"
)
```

### 4. Use with Browser-Use

```python
from browser_use.integrations.gmail import register_gmail_actions
from browser_use import Agent, Controller
from browser_use.llm import ChatOpenAI

# Get token from Lambda
client = GmailTokenClient('gmail-token-manager', 'us-east-1')
token_result = client.get_token('your-user-id')
access_token = token_result['access_token']

# Register Gmail actions with the token
controller = Controller()
register_gmail_actions(controller, access_token=access_token)

# Use the agent
agent = Agent(
    task="Find any 2FA codes in my recent emails",
    llm=ChatOpenAI(model="gpt-4o"),
    controller=controller
)

history = await agent.run()
```

## 📡 **API Reference**

### Store Credentials

Store Gmail OAuth credentials for a user.

```json
{
    "user_id": "unique_user_identifier",
    "action": "store_credentials",
    "credentials": {
        "client_id": "your-client-id.apps.googleusercontent.com",
        "client_secret": "your-client-secret",
        "refresh_token": "your-refresh-token",
        "access_token": "optional-initial-token",
        "expires_at": "2024-01-01T12:00:00Z"
    }
}
```

**Response:**
```json
{
    "statusCode": 200,
    "body": {
        "message": "Credentials stored successfully",
        "user_id": "unique_user_identifier"
    }
}
```

### Get Token

Get a valid access token (auto-refreshes if expires in < 30 minutes).

```json
{
    "user_id": "unique_user_identifier",
    "action": "get_token"
}
```

**Response:**
```json
{
    "statusCode": 200,
    "body": {
        "access_token": "ya29.a0AfH6SMXXXXXXXX",
        "expires_at": "2024-01-01T13:00:00Z",
        "valid_for_minutes": 45,
        "refreshed": false
    }
}
```

### Force Refresh Token

Force refresh the access token immediately.

```json
{
    "user_id": "unique_user_identifier",
    "action": "refresh_token"
}
```

**Response:**
```json
{
    "statusCode": 200,
    "body": {
        "access_token": "ya29.a0AfH6SMXXXXXXXX",
        "expires_at": "2024-01-01T14:00:00Z",
        "valid_for_minutes": 60,
        "refreshed": true
    }
}
```

## 🗄️ **DynamoDB Schema**

Table: `gmail-credentials`

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | String (PK) | Unique user identifier |
| `client_id` | String | Google OAuth client ID |
| `client_secret` | String | Google OAuth client secret |
| `refresh_token` | String | Google OAuth refresh token |
| `access_token` | String | Current access token |
| `expires_at` | Number | Token expiry timestamp |
| `created_at` | String | Record creation time |
| `updated_at` | String | Last update time |

## 🛠️ **Configuration**

### Environment Variables

- `DYNAMODB_TABLE_NAME`: DynamoDB table name (default: `gmail-credentials`)
- `AWS_REGION`: AWS region (auto-detected in Lambda)

### Lambda Configuration

- **Runtime**: Python 3.11
- **Memory**: 256 MB
- **Timeout**: 30 seconds
- **Execution Role**: Auto-created with DynamoDB permissions

## 🔒 **Security**

- **IAM Permissions**: Minimal permissions (DynamoDB read/write only)
- **Encryption**: DynamoDB encryption at rest
- **Network**: No VPC access required
- **Secrets**: OAuth secrets stored in DynamoDB (consider AWS Secrets Manager for production)

## 🧪 **Testing**

### Test the Lambda Function

```bash
# Test with AWS CLI
aws lambda invoke \
    --function-name gmail-token-manager \
    --payload '{"user_id":"test-user","action":"get_token"}' \
    response.json && cat response.json
```

### Run Usage Examples

```bash
# Install dependencies for local testing
pip install boto3

# Run examples (requires deployed Lambda)
python usage_example.py
```

## 🔧 **Troubleshooting**

### Common Issues

1. **"No credentials found"**
   - Make sure you've stored credentials first using `store_credentials`

2. **"Token refresh failed"**
   - Check that your refresh token is valid
   - Ensure Google OAuth client is properly configured

3. **"Lambda timeout"**
   - Network issues with Google OAuth API
   - Consider increasing timeout in Lambda configuration

4. **Permission denied errors**
   - Check IAM role has DynamoDB permissions
   - Verify table name matches environment variable

### Logs

View Lambda logs in CloudWatch:
```bash
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/gmail-token-manager
aws logs tail /aws/lambda/gmail-token-manager --follow
```

## 📈 **Scaling & Performance**

- **Concurrent Executions**: Default 1000 (can be increased)
- **Cold Start**: ~2-3 seconds for first request
- **Warm Execution**: ~100-200ms
- **DynamoDB**: Auto-scaling with pay-per-request billing

## 💰 **Cost Estimation**

Based on AWS pricing (us-east-1):

- **Lambda**: $0.0000166667 per GB-second
- **DynamoDB**: $1.25 per million requests
- **Data Transfer**: $0.09 per GB (outbound)

**Example monthly cost for 10,000 token requests:**
- Lambda: ~$0.50
- DynamoDB: ~$0.01
- **Total: ~$0.51/month**

## 🔄 **Updates & Maintenance**

### Update Lambda Function

```bash
# Redeploy with latest changes
./deploy.sh
```

### Update Dependencies

```bash
# Edit requirements.txt, then redeploy
./deploy.sh
```

### Monitor

- CloudWatch metrics for Lambda performance
- DynamoDB metrics for read/write capacity
- Set up alarms for error rates

## 🤝 **Integration Examples**

### With Browser-Use

```python
# production_example.py
import asyncio
from usage_example import GmailTokenClient

async def get_fresh_gmail_token(user_id: str) -> str:
    """Get a fresh Gmail token for browser-use integration"""
    client = GmailTokenClient('gmail-token-manager', 'us-east-1')
    result = client.get_token(user_id)
    return result['access_token']

# Use in your browser-use workflow
token = await get_fresh_gmail_token('user123')
register_gmail_actions(controller, access_token=token)
```

### With FastAPI

```python
from fastapi import FastAPI
from usage_example import GmailTokenClient

app = FastAPI()
client = GmailTokenClient('gmail-token-manager')

@app.get("/gmail-token/{user_id}")
async def get_gmail_token(user_id: str):
    try:
        result = client.get_token(user_id)
        return {"token": result['access_token'], "expires_at": result['expires_at']}
    except Exception as e:
        return {"error": str(e)}, 400
```

## 📝 **Contributing**

1. Fork the repository
2. Create a feature branch
3. Test your changes
4. Submit a pull request

## 📄 **License**

This project is part of the browser-use package and follows the same license.

---

**🎉 Ready to use!** Your Gmail Token Manager Lambda function is now set up and ready to handle OAuth token management for your browser-use applications. 