import urllib.request, json

resp = urllib.request.urlopen('http://127.0.0.1:8000/protocol')
body = resp.read().decode()
print('RESPONSE_BODY:')
print(body)
print('\nHTTP_STATUS: %s' % resp.getcode())
