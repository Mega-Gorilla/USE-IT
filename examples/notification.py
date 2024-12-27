import yagmail

# STEP 1: go to https://support.google.com/accounts/answer/185833
# STEP 2: Create an app password
# STEP 3: Use the app password in the code below for the password
yag = yagmail.SMTP('your_email@gmail.com', 'your_app_password')
yag.send(
	to='recipient@example.com', subject='Test Email', contents='Hello from Python using yagmail!'
)
print('Email sent!')
