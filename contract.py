import datetime as dt
import eospy.cleos
import eospy.keys
import pytz
from requests import HTTPError
from settings import user_param


def push_transaction(params_json):
    # this url is to a testnet that may or may not be working.
    # We suggest using a different testnet such as kylin or jungle
    #
    ce = eospy.cleos.Cleos(url=user_param.rpc_domain)
    payloads = []
    for item in params_json['actions']:
        arguments = item['data']
        payload = {
            "account": item['account'],
            "name": item['name'],
            "authorization": get_authorization(),
        }

        # Converting payload to binary
        data = ce.abi_json_to_bin(payload['account'], payload['name'], arguments)
        # Inserting payload binary form as "data" field in original payload
        payload['data'] = data['binargs']
        payloads.append(payload)
    # final transaction formed
    trx = {
        "actions": payloads,
        'expiration': str((dt.datetime.utcnow() + dt.timedelta(seconds=60)).replace(tzinfo=pytz.UTC))
    }
    # use a string or EOSKey for push_transaction
    # key = "5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3"
    # use EOSKey:

    # key = eospy.keys.EOSKey(user_param.private_key)
    try:
        resp = ce.push_transaction(trx, get_keys(), broadcast=True)
        return True, resp
    except HTTPError as e:
        return False, str(e)


def get_authorization():
    authorization = []
    if user_param.use_pay_agent:
        authorization.append({
            "actor": user_param.pay_wax_account,
            "permission": "active",
        })

    authorization.append({
        "actor": user_param.wax_account,
        "permission": "active",
    })
    return authorization


def get_keys():
    keys = []
    if user_param.use_pay_agent:
        keys.append(eospy.keys.EOSKey(user_param.pay_private_key))

    keys.append(eospy.keys.EOSKey(user_param.private_key))
    return keys
