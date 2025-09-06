import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()


from browser_use import Agent

secret_key = os.environ.get('OTP_SECRET_KEY')
if not secret_key:
	# For this example copy the code from the website https://authenticationtest.com/totpChallenge/
	# For real 2fa just copy the secret key when you setup 2fa, you can get this e.g. in 1Password
	secret_key = 'JBSWY3DPEHPK3PXP'


sensitive_data = {'otp_secret': secret_key}


task = """
1. Go to https://authenticationtest.com/totpChallenge/ and log in.
2. Use the the secret otp_secret to generate the 2FA code."""


Agent(task=task, sensitive_data=sensitive_data).run_sync()  # type: ignore
