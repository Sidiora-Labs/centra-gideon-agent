import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createReadStream, existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { basename, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export function approvedOrigin(value) {
  const url = new URL(value);
  assert.equal(url.protocol, 'https:', 'GIDEON_CLOUD_URL must use HTTPS');
  assert.equal(url.href, `${url.origin}/`, 'GIDEON_CLOUD_URL must be a bare HTTPS origin');
  assert.equal(url.username, '');
  assert.equal(url.password, '');
  return url.origin;
}

export function inspectInstall(installDir, expectedUrl) {
  const configPath = join(installDir, 'resources', 'hosted-config.json');
  const executable = join(installDir, 'Gideon.exe');
  const uninstaller = join(installDir, 'Uninstall Gideon.exe');
  assert.ok(existsSync(executable), `installed executable missing: ${executable}`);
  assert.ok(existsSync(uninstaller), `NSIS uninstaller missing: ${uninstaller}`);
  const config = JSON.parse(readFileSync(configPath, 'utf8'));
  assert.deepEqual(config, { schemaVersion: 1, mode: 'hosted', hostedUrl: approvedOrigin(expectedUrl) });
  assert.ok(existsSync(join(installDir, 'resources', 'app.asar')), 'packaged app.asar missing');
  assert.ok(!existsSync(join(installDir, 'resources', 'backend-dist')), 'Windows hosted install contains a local backend');
  return { executable, uninstaller, configPath, config };
}

export function findInstaller(distDir) {
  const candidates = readdirSync(distDir).filter((name) => /^Gideon-Setup-.*-x64\.exe$/i.test(name));
  assert.equal(candidates.length, 1, `expected one x64 NSIS installer in ${distDir}; found ${candidates.join(', ')}`);
  return join(distDir, candidates[0]);
}

export async function sha256(file) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

export async function verifyArtifact({ distDir, installDir, url, reportDir }) {
  const installer = findInstaller(distDir);
  const header = readFileSync(installer).subarray(0, 2).toString('ascii');
  assert.equal(header, 'MZ', 'installer must be a Windows PE executable');
  const installed = inspectInstall(installDir, url);
  const digest = await sha256(installer);
  const buildManifest = readFileSync(join(distDir, 'SHA256SUMS.txt'), 'utf8').trim();
  assert.equal(buildManifest, `${digest}  ${basename(installer)}`, 'build checksum manifest disagrees with installer');
  const signature = execFileSync('powershell', ['-NoProfile', '-Command',
    '(Get-AuthenticodeSignature -LiteralPath $env:GIDEON_VERIFY_INSTALLER).Status.ToString()'],
  { encoding: 'utf8', env: { ...process.env, GIDEON_VERIFY_INSTALLER: installer } }).trim();
  const result = { runner: process.platform, installer: basename(installer), sha256: digest,
    installedExecutable: installed.executable, config: installed.config, signature };
  writeFileSync(join(reportDir, 'SHA256SUMS.txt'), `${digest}  ${basename(installer)}\n`);
  writeFileSync(join(reportDir, 'windows-proof.json'), `${JSON.stringify(result, null, 2)}\n`);
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    assert.equal(process.platform, 'win32', 'artifact installation proof requires a Windows runner');
    const [distDir, installDir, url, reportDir] = process.argv.slice(2);
    assert.ok(distDir && installDir && url && reportDir, 'usage: verify-windows-artifact.mjs DIST INSTALL URL REPORT');
    const result = await verifyArtifact({ distDir, installDir, url, reportDir });
    console.log(`Verified installed Gideon and ${result.installer}: ${result.sha256}`);
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
}
