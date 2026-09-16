import Foundation
import Darwin

struct ConfigValidation {
    static func prepare(original: String, updated: String, provider: String, linked: Bool, grok: Bool) throws -> String {
        let result = try PythonRuntime.run(#"""
import copy,json,re,sys,tomllib
try:
    request=json.load(sys.stdin)
    original=tomllib.loads(request['original'])
    text=request['updated']
    if request['grok']:
        marker='# Codex Model Switcher Grok Responses adapter'
        name='xai-switcher'
        source=original.get('model_providers',{}).get('xai')
        if not source or source.get('base_url','').rstrip('/')!='https://api.x.ai/v1':
            raise ValueError('Grok requires an existing xai provider using https://api.x.ai/v1.')
        if name in original.get('model_providers',{}) and marker not in text:
            raise ValueError('The xai-switcher provider name is already owned by another configuration.')
        lines=text.splitlines()
        if marker in lines:
            start=lines.index(marker);end=start+1
            while end<len(lines):
                line=lines[end].strip()
                if line.startswith('[') and not re.match(r'^\[(?:"model_providers"|model_providers)\.(?:"xai-switcher"|xai-switcher)(?:\.|\])',line):break
                end+=1
            del lines[start:end]
        provider=copy.deepcopy(source)
        provider.update(name='Grok via Codex Model Switcher',base_url='http://127.0.0.1:48118/v1',supports_websockets=False,wire_api='responses')
        def table(path,value):
            result=['['+'.'.join(json.dumps(p) for p in path)+']']
            for key,item in value.items():
                if not isinstance(item,dict):result.append(json.dumps(key)+' = '+json.dumps(item))
            for key,item in value.items():
                if isinstance(item,dict):result+=['']+table(path+[key],item)
            return result
        text='\n'.join(lines+['',marker]+table(['model_providers',name],provider))+'\n'
    parsed=tomllib.loads(text)
    before=copy.deepcopy(original);after=copy.deepcopy(parsed)
    for key in ['model','model_provider','model_catalog_json','cli_auth_credentials_store','model_reasoning_effort']:
        before.pop(key,None);after.pop(key,None)
    allowed=[] if request['linked'] else [request['provider']]
    if request['grok']:allowed.append('xai-switcher')
    for name in allowed:
        before.get('model_providers',{}).pop(name,None)
        after.get('model_providers',{}).pop(name,None)
    if not before.get('model_providers'):before.pop('model_providers',None)
    if not after.get('model_providers'):after.pop('model_providers',None)
    if before!=after:raise ValueError('The edit would change unrelated configuration. Nothing was written.')
    print(text,end='')
except Exception as error:
    print('Configuration validation failed: '+str(error),file=sys.stderr)
    sys.exit(1)
"""#, input: ["original": original, "updated": updated, "provider": provider, "linked": linked, "grok": grok])
        return String(decoding: result, as: UTF8.self)
    }
}

final class ConfigLock {
    private var descriptor: Int32 = -1
    init(directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        descriptor = open(directory.appendingPathComponent("model-switcher.lock").path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            descriptor = -1
            throw NSError(domain: "Switcher", code: 4, userInfo: [NSLocalizedDescriptionKey: "Another switch is in progress. Try again when it finishes."])
        }
    }
    deinit { if descriptor >= 0 { flock(descriptor, LOCK_UN); close(descriptor) } }
}
