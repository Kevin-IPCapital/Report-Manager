// Quick smoke test for the N-central REST API using credentials from .env
// Run with: node --env-file=.env test-connection.js

const BASE = process.env.NCENTRAL_API_BASE_URL;
const JWT = process.env.NCENTRAL_JWT;

async function main() {
  if (!BASE || !JWT) {
    throw new Error('Missing NCENTRAL_API_BASE_URL or NCENTRAL_JWT in .env');
  }

  console.log(`Base URL: ${BASE}`);

  // 1. Health check (no auth)
  const health = await fetch(`${BASE}/api/health`);
  console.log('GET /api/health ->', health.status, await health.text());

  // 2. Exchange JWT for access token
  const authRes = await fetch(`${BASE}/api/auth/authenticate`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${JWT}` },
  });
  const authBody = await authRes.json().catch(() => ({}));
  console.log('POST /api/auth/authenticate ->', authRes.status, authBody);

  if (!authRes.ok) {
    console.error('Authentication failed, stopping here.');
    return;
  }

  const accessToken = authBody.tokens?.access?.token || authBody.access?.token || authBody.accessToken;
  if (!accessToken) {
    console.error('Could not find access token in response shape:', authBody);
    return;
  }

  // 3. List service organizations
  const soRes = await fetch(`${BASE}/api/service-orgs?pageSize=10`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const soBody = await soRes.json().catch(() => ({}));
  console.log('GET /api/service-orgs ->', soRes.status, JSON.stringify(soBody, null, 2));
}

main().catch((err) => {
  console.error('Error:', err.message);
  process.exit(1);
});
