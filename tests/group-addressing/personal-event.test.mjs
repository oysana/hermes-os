import test from 'node:test';
import assert from 'node:assert/strict';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const source = process.env.WA_PERSONAL_EVENT_SOURCE || resolve(homedir(), '.hermes/profiles/yqecea/wa-reader/personal-event.mjs');
const { personalEventMetadata, freshIncomingSource, addressedQuestion, shouldCaptureMessage } = await import(pathToFileURL(source));
const owner = {id:'123:4@s.whatsapp.net',lid:'456@lid'};
const noBot = () => false;

test('stable identity and exact native owner mentions survive metadata', () => {
  const meta = personalEventMetadata({key:{id:'msg1',remoteJid:'789@lid'},message:{extendedTextMessage:{contextInfo:{mentionedJid:['123@s.whatsapp.net']}}}},owner,noBot);
  assert.equal(meta.id,'msg1'); assert.equal(meta.addressed,true);
});
test('caption replies to native owner LID are addressed', () => {
  for (const kind of ['imageMessage','videoMessage','documentMessage']) {
    const meta=personalEventMetadata({key:{id:'caption'},message:{[kind]:{caption:'Hi',contextInfo:{participant:'456@lid'}}}},owner,noBot);
    assert.equal(meta.addressed,true);
  }
});
test('unrelated mention and bot identity are kept distinct', () => {
  const meta=personalEventMetadata({key:{remoteJid:'bot@c.us'},message:{extendedTextMessage:{contextInfo:{mentionedJid:['999@lid']}}}},owner, id=>id==='bot@c.us');
  assert.equal(meta.addressed,false); assert.equal(meta.isBot,true);
});
test('fresh native LID and group sources become known without arbitrary targets', () => {
  for (const id of ['789@lid','123-456@g.us','789@s.whatsapp.net']) assert.equal(freshIncomingSource({key:{remoteJid:id,fromMe:false}},'notify'),id);
  assert.equal(freshIncomingSource({key:{remoteJid:'789@lid'}},'history'),null);
  assert.equal(freshIncomingSource({key:{remoteJid:'789@lid',fromMe:true}},'notify'),null);
  assert.equal(freshIncomingSource({key:{remoteJid:'evil@example.com'}},'notify'),null);
});

test('shared direct-name question aliases are bounded and third-person chatter stays quiet',()=>{
 for(const text of ['Йусуф, какая API?','Юсуф как дела?','YUSUF what API?','@yusuf сенде херместе чеза апишка тұр','Йусуф, 2+2?','Yusuf, API?'])assert.equal(addressedQuestion(text),true,text);
 for(const text of ['Кто знает API Йусуфа?','Yusufovich what API?','Йусуф был вчера здесь','Спасибо Йусуф','Yusuf спасибо','Yusuf is offline','Йусуф не знает эту API','Йусуф сказал вчера, какая API у Ахмета?','Юсуф ответил вчера. Какая API у Ахмета?','Yusuf is offline. Which API does Ahmet use?'])assert.equal(addressedQuestion(text),false,text);
 assert.equal(addressedQuestion('Какая API?',true),true);
});

test('uncaptioned native documents and voice notes remain captured without invented text', async () => {
  for (const [kind, voice] of [['documentMessage',false],['audioMessage',true],['imageMessage',false]]) {
    const meta=personalEventMetadata({key:{id:'attachment'},message:{[kind]:{fileName:'original.bin',mimetype:'application/octet-stream',fileLength:123,ptt:voice}}},owner,noBot);
    assert.equal(meta.media.kind,kind);
    assert.equal(meta.media.name,'original.bin');
    assert.equal(meta.media.size,123);
    assert.equal(meta.media.voice,voice);
    assert.equal(shouldCaptureMessage({...meta,text:'',fromMe:false}),true);
    assert.equal(meta.addressed_question,false);
  }
  assert.equal(shouldCaptureMessage({text:'',fromMe:false}),false);
});
