import asyncio

from pydantic import BaseModel

from browser_use import Browser, ChatOpenAI

llm = ChatOpenAI('gpt-4.1-mini')


async def main():
	"""
	Main function demonstrating mixed automation with Browser-Use and Playwright.
	"""
	print('🚀 Mixed Automation with Browser-Use and Actor API')

	browser = Browser(keep_alive=True)
	await browser.start()

	target = await browser.get_current_target() or await browser.new_target()

	await target.navigate('https://www.nseindia.com/companies-listing/corporate-filings-financial-results-comparision')

	company_name_input = await target.must_get_element_by_prompt('company name input', llm)
	await company_name_input.fill('Tata Consultancy Services Limited')

	await asyncio.sleep(1)

	confirm_button = await target.must_get_element_by_prompt('confirm Tata Consultancy Services Limited selection button', llm)
	await confirm_button.click()

	await asyncio.sleep(0.1)

	search_button = await target.must_get_element_by_prompt('search button', llm)
	await search_button.click()

	await asyncio.sleep(2)  # wait for the table to load

	class Quarter(BaseModel):
		quarter: str
		year: str
		net_profit: str

	class TableData(BaseModel):
		qurters: list[Quarter]

	extracted_table = await target.extract_content('extract the table', TableData, llm)
	print(extracted_table)

	await asyncio.sleep(10)

	input('Press Enter to continue...')

	# await browser.stop()


if __name__ == '__main__':
	asyncio.run(main())
