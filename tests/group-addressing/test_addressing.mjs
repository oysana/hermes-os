import { homedir } from 'node:os';
import { resolve } from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const source = process.env.WA_PERSONAL_EVENT_SOURCE || resolve(homedir(), '.hermes/profiles/yqecea/wa-reader/personal-event.mjs');
const { addressedQuestion, personalEventMetadata } = await import(pathToFileURL(source));
for (const fixture of JSON.parse(readFileSync(new URL('./cases.json',import.meta.url),'utf8'))) {
 test(fixture.id, () => assert.equal(addressedQuestion(fixture.text), fixture.expected));
}
test('native owner mention/reply metadata remains independent of lexical aliases', () => {
 const owner={id:'123:4@s.whatsapp.net',lid:'456@lid'};
 for (const contextInfo of [{mentionedJid:['123@s.whatsapp.net']},{participant:'456@lid'}]) {
  const meta=personalEventMetadata({key:{id:'synthetic'},message:{extendedTextMessage:{text:'Какая API?',contextInfo}}},owner,()=>false);
  assert.equal(meta.addressed,true);assert.equal(meta.addressed_question,true);
 }
 const unrelated=personalEventMetadata({key:{id:'synthetic'},message:{extendedTextMessage:{text:'Какая API?',contextInfo:{mentionedJid:['999@lid']}}}},owner,()=>false);
 assert.equal(unrelated.addressed,false);assert.equal(unrelated.addressed_question,false);
 assert.equal(addressedQuestion('«Кто видел https://example.com/?to=@yusuf?»',true),true);
 assert.equal(addressedQuestion('Спасибо',true),false);
 assert.equal(addressedQuestion(' '.repeat(4000)+'?',true),false);
});
