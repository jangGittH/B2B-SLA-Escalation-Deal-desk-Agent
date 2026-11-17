import time


def wait_workflow(client,path,headers,status='waiting_for_approval'):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        response=client.get(path,headers=headers)
        assert response.status_code==200,response.text
        row=response.json()
        if row['status']==status:return row
        time.sleep(.05)
    raise AssertionError(f'Workflow did not reach {status}: {row}')
