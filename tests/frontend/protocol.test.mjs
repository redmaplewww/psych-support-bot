// 协议常量交叉校验：protocol.js（前端镜像）↔ 服务端导出的 JSON Schema
// （docs/technical/voice-protocol.schema.json，由 scripts/export_voice_protocol.py
// 从 infra/voice/protocol.py 生成）。任一侧事件名漂移即红。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import * as proto from '../../src/psych_support_bot/static/js/voice/protocol.js';

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = join(here, '..', '..', 'docs', 'technical', 'voice-protocol.schema.json');
const schema = JSON.parse(readFileSync(schemaPath, 'utf8'));

function discriminatorConsts(node, acc = []) {
  if (!node || typeof node !== 'object') return acc;
  if (node.properties && node.properties.type && node.properties.type.const) {
    acc.push(node.properties.type.const);
  }
  for (const value of Object.values(node)) discriminatorConsts(value, acc);
  return acc;
}

test('客户端事件常量与服务端 schema 一致', () => {
  const serverSide = discriminatorConsts(schema.client_message);
  assert.deepEqual(serverSide.sort(), Object.values(proto.CLIENT).sort());
});

test('服务端事件常量与 schema 一致', () => {
  const serverSide = discriminatorConsts(schema.server_event);
  assert.deepEqual(serverSide.sort(), Object.values(proto.SERVER).sort());
});

test('构造器产出的线格式与服务端模型序列化逐字一致', () => {
  assert.deepEqual(proto.say('你好'), { type: 'say', text: '你好' });
  assert.deepEqual(proto.end(), { type: 'end' });
  assert.deepEqual(proto.abort(), { type: 'abort' });
  assert.deepEqual(proto.ready(24000), { type: 'ready', audio: { format: 'pcm', sample_rate: 24000 } });
  assert.deepEqual(
    proto.ready(24000, { first_audio_timeout_ms: 20000 }),
    { type: 'ready', audio: { format: 'pcm', sample_rate: 24000 }, tts: { first_audio_timeout_ms: 20000 } },
  );
  assert.deepEqual(proto.sentenceEnd(), { type: 'sentence_end' });
  assert.deepEqual(proto.roundEnd(), { type: 'round_end' });
  assert.deepEqual(proto.error('boom'), { type: 'error', detail: 'boom' });
});
