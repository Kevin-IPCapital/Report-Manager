// Smoke test: authenticate, then GET /api/org-units?pageSize=500&pageNumber=1,
// and print totalItems plus the first 3 CUSTOMER-type org units.
// Run with: node --env-file=.env smoke-test.js

const { getAccessToken, request } = require('./ncentral-client');

async function main() {
  const accessToken = await getAccessToken();
  console.log('Authenticated. Access token (truncated):', accessToken.slice(0, 12) + '...');

  const page = await request('/api/org-units?pageSize=500&pageNumber=1');
  console.log('totalItems:', page.totalItems);

  const firstThreeCustomers = page.data.filter((ou) => ou.orgUnitType === 'CUSTOMER').slice(0, 3);
  console.log('First 3 CUSTOMER org units:', JSON.stringify(firstThreeCustomers, null, 2));
}

main().catch((err) => {
  console.error('Error:', err.message);
  process.exit(1);
});
