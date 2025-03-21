#!/usr/bin/env python3

################################################################################################
#
# GlacierScript:  Part of the Glacier Protocol (http://glacierprotocol.org)
#
# GlacierScript is designed specifically for use in the context of executing the broader Glacier
# Protocol, a step-by-step procedure for high-security cold storage of Bitcoin.  It is not
# intended to be used as standalone software.
#
# GlacierScript primarily replaces tasks that users would otherwise be doing manually, such as
# typing things on the command line, copying-and-pasting strings, and hand-editing JSON.  It
# mostly consists of print statements, user input, string & JSON manipulation, and command-line
# wrappers around Bitcoin Core and other applications (e.g. those involved in reading and writing
# QR codes.)
#
# GlacierScript avoids cryptographic and other security-sensitive operations as much as possible.
#
# GlacierScript depends on the following command-line applications:
# - Bitcoin Core (http://bitcoincore.org)
# - qrencode (QR code writer: http://packages.ubuntu.com/xenial/qrencode)
# - zbarimg (QR code reader: http://packages.ubuntu.com/xenial/zbar-tools)
#
################################################################################################

# standard Python libraries
import argparse
from collections import OrderedDict
from decimal import Decimal
import glob
from hashlib import sha256, md5
import json
import os
import shlex
import subprocess
import sys
import time

# Taken from https://github.com/keis/base58
from base58 import b58encode_check

SATOSHI_PLACES = Decimal("0.00000001")

verbose_mode = 1

################################################################################################
#
# Minor helper functions
#
################################################################################################

def hash_sha256(s):
    """A thin wrapper around the hashlib SHA256 library to provide a more functional interface"""
    m = sha256()
    m.update(s.encode('ascii'))
    return m.hexdigest()


def hash_md5(s):
    """A thin wrapper around the hashlib md5 library to provide a more functional interface"""
    m = md5()
    m.update(s.encode('ascii'))
    return m.hexdigest()


def satoshi_to_btc(satoshi):
    """
    Converts a value in satoshi to a value in BTC
    outputs => Decimal

    satoshi: <int>
    """
    value = Decimal(satoshi) / Decimal(100000000)
    return value.quantize(SATOSHI_PLACES)


def btc_to_satoshi(btc):
    """
    Converts a value in BTC to satoshi
    outputs => <int>

    btc: <Decimal> or <Float>
    """
    value = btc * 100000000
    return int(value)


################################################################################################
#
# Subprocess helper functions
#
################################################################################################

def verbose(content):
    if verbose_mode: print(content)


def run_subprocess(exe, *args):
    """
    Run a subprocess (bitcoind or bitcoin-cli)
    Returns => (command, return code, output)

    exe: executable file name (e.g. bitcoin-cli)
    args: arguments to exe
    """
    cmd_list = [exe] + cli_args + list(args)
    verbose("bitcoin cli call:\n  {0}\n".format(" ".join(shlex.quote(x) for x in cmd_list)))
    with subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as pipe:
        output, _ = pipe.communicate()
    output = output.decode('ascii')
    retcode = pipe.returncode
    verbose("bitcoin cli call return code: {0}  output:\n  {1}\n".format(retcode, output))
    return (cmd_list, retcode, output)


def bitcoin_cli_call(*args):
    """
    Run `bitcoin-cli`, return OS return code
    """
    _, retcode, _ = run_subprocess("bitcoin-cli", *args)
    return retcode


def bitcoin_cli_checkoutput(*args):
    """
    Run `bitcoin-cli`, fail if OS return code nonzero, return output
    """
    cmd_list, retcode, output = run_subprocess("bitcoin-cli", *args)
    if retcode != 0: raise subprocess.CalledProcessError(retcode, cmd_list, output=output)
    return output


def bitcoin_cli_json(*args):
    """
    Run `bitcoin-cli`, parse output as JSON
    """
    return json.loads(bitcoin_cli_checkoutput(*args))


def bitcoind_call(*args):
    """
    Run `bitcoind`, return OS return code
    """
    _, retcode, _ = run_subprocess("bitcoind", *args)
    return retcode


################################################################################################
#
# Read & validate random data from the user
#
################################################################################################

def validate_rng_seed(seed, min_length):
    """
    Validates random hexadecimal seed
    returns => <boolean>

    seed: <string> hex string to be validated
    min_length: <int> number of characters required.  > 0
    """

    if len(seed) < min_length:
        print("Error: Computer entropy must be at least {0} characters long".format(min_length))
        return False

    if len(seed) % 2 != 0:
        print("Error: Computer entropy must contain an even number of characters.")
        return False

    try:
        int(seed, 16)
    except ValueError:
        print("Error: Illegal character. Computer entropy must be composed of hexadecimal characters only (0-9, a-f).")
        return False

    return True


def read_rng_seed_interactive(min_length):
    """
    Reads random seed (of at least min_length hexadecimal characters) from standard input
    returns => string

    min_length: <int> minimum number of bytes in the seed.
    """

    char_length = min_length * 2

    def ask_for_rng_seed(length):
        print("Enter at least {0} characters of computer entropy. Spaces are OK, and will be ignored:".format(length))

    ask_for_rng_seed(char_length)
    seed = input()
    seed = unchunk(seed)

    while not validate_rng_seed(seed, char_length):
        ask_for_rng_seed(char_length)
        seed = input()
        seed = unchunk(seed)

    return seed


def validate_dice_seed(dice, min_length):
    """
    Validates dice data (i.e. ensures all digits are between 1 and 6).
    returns => <boolean>

    dice: <string> representing list of dice rolls (e.g. "5261435236...")
    """

    if len(dice) < min_length:
        print("Error: You must provide at least {0} dice rolls".format(min_length))
        return False

    for die in dice:
        try:
            i = int(die)
            if i < 1 or i > 6:
                print("Error: Dice rolls must be between 1 and 6.")
                return False
        except ValueError:
            print("Error: Dice rolls must be numbers between 1 and 6")
            return False

    return True


def read_dice_seed_interactive(min_length):
    """
    Reads min_length dice rolls from standard input, as a string of consecutive integers
    Returns a string representing the dice rolls
    returns => <string>

    min_length: <int> number of dice rolls required.  > 0.
    """

    def ask_for_dice_seed(x):
        print("Enter {0} dice rolls (example: 62543 16325 21341...) Spaces are OK, and will be ignored:".format(x))

    ask_for_dice_seed(min_length)
    dice = input()
    dice = unchunk(dice)

    while not validate_dice_seed(dice, min_length):
        ask_for_dice_seed(min_length)
        dice = input()
        dice = unchunk(dice)

    return dice


################################################################################################
#
# private key generation
#
################################################################################################

def xor_hex_strings(str1, str2):
    """
    Return xor of two hex strings.
    An XOR of two pieces of data will be as random as the input with the most randomness.
    We can thus combine two entropy sources in this way as a safeguard against one source being
    compromised in some way.
    For details, see http://crypto.stackexchange.com/a/17660

    returns => <string> in hex format
    """
    if len(str1) != len(str2):
        raise Exception("tried to xor strings of unequal length")
    str1_dec = int(str1, 16)
    str2_dec = int(str2, 16)

    xored = str1_dec ^ str2_dec

    return "{:0{}x}".format(xored, len(str1))


def hex_private_key_to_WIF_private_key(hex_key):
    """
    Converts a raw 256-bit hex private key to WIF format
    returns => <string> in hex format
    """
    hex_key_with_prefix = wif_prefix + hex_key + "01"
    wif_key = b58encode_check(bytes.fromhex(hex_key_with_prefix))
    return wif_key.decode('ascii')


################################################################################################
#
# Bitcoin helper functions
#
################################################################################################

def ensure_bitcoind_running():
    """
    Start bitcoind (if it's not already running) and ensure it's functioning properly
    """
    # start bitcoind.  If another bitcoind process is already running, this will just print an error
    # message (to /dev/null) and exit.
    #
    # -connect=0.0.0.0 because we're doing local operations only (and have no network connection anyway)
    bitcoind_call("-daemon", "-connect=0.0.0.0")

    # give bitcoind time to start - needed for tests to pass
    time.sleep(1)

    # verify bitcoind started up and is functioning correctly
    times = 0
    while times <= 20:
        times += 1
        if bitcoin_cli_call("getnetworkinfo") == 0:
            create_default_wallet()
            return
        time.sleep(0.5)

    raise Exception("Timeout while starting bitcoin server")


def create_default_wallet():
    """
    Ensure the default wallet exists and is loaded.

    Since v0.21, Bitcoin Core will not create a default wallet when
    started for the first time.
    """
    loaded_wallets = bitcoin_cli_json("listwallets")
    if "" in loaded_wallets:
        return  # default wallet already loaded
    all_wallets = bitcoin_cli_json("listwalletdir")
    # {
    #     "wallets": [
    #         {
    #             "name": ""
    #         }
    #     ]
    # }
    found = any(w["name"] == "" for w in all_wallets["wallets"])
    cmd = "loadwallet" if found else "createwallet"
    loaded_wallet = bitcoin_cli_json(cmd, "")
    if len(loaded_wallet["warning"]):
        raise Exception("problem running {} on default wallet".format(cmd))  # pragma: no cover


def require_minimum_bitcoind_version(min_version):
    """
    Fail if the bitcoind version in use is older than required
    <min_version> - required minimum version in format of getnetworkinfo, i.e. 150100 for v0.15.1
    """
    networkinfo = bitcoin_cli_json("getnetworkinfo")

    if int(networkinfo["version"]) < min_version:
        print("ERROR: Your bitcoind version is too old. You have {}, I need {} or newer. Exiting...".format(networkinfo["version"], min_version))
        sys.exit()

def get_address_for_wif_privkey(privkey):
    """A method for retrieving the address associated with a private key from bitcoin core
       <privkey> - a bitcoin private key in WIF format"""

    # Bitcoin Core doesn't have an RPC for "get the addresses associated w/this private key"
    # just "get the addresses associated with this label"
    # where "label" corresponds to an arbitrary tag we can associate with each private key
    # so, we'll generate a unique "label" to attach to this private key.

    label = hash_sha256(privkey)

    ensure_bitcoind_running()
    bitcoin_cli_call("importprivkey", privkey, label)
    addresses = bitcoin_cli_json("getaddressesbylabel", label)

    # getaddressesbylabel returns multiple addresses associated with
    # this one privkey; since we use it only for communicating the
    # pubkey to addmultisigaddress, it doesn't matter which one we
    # choose; they are all associated with the same pubkey.

    return next(iter(addresses))


def addmultisigaddress(m, addresses_or_pubkeys, address_type='p2sh-segwit'):
    """
    Call `bitcoin-cli addmultisigaddress`
    returns => JSON response from bitcoin-cli

    m: <int> number of multisig keys required for withdrawal
    addresses_or_pubkeys: List<string> either addresses or hex pubkeys for each of the N keys
    """
    address_string = json.dumps(addresses_or_pubkeys)
    return bitcoin_cli_json("addmultisigaddress", str(m), address_string, "", address_type)

def get_utxos(tx, address):
    """
    Given a transaction, find all the outputs that were sent to an address
    returns => List<Dictionary> list of UTXOs in bitcoin core format

    tx - <Dictionary> in bitcoin core format
    address - <string>
    """
    utxos = []

    for output in tx["vout"]:
        if "address" not in output["scriptPubKey"]:
            # In Bitcoin Core versions older than v22.0, the 'address' field did not exist
            continue
        if address == output["scriptPubKey"]["address"]:
            utxos.append(output)

    return utxos


def create_unsigned_transaction(source_address, destinations, redeem_script, input_txs):
    """
    Returns a hex string representing an unsigned bitcoin transaction
    returns => <string>

    source_address: <string> input_txs will be filtered for utxos to this source address
    destinations: {address <string>: amount<string>} dictionary mapping destination addresses to amount in BTC
    redeem_script: <string>
    input_txs: List<dict> List of input transactions in dictionary form (bitcoind decoded format)
    """
    ensure_bitcoind_running()

    # prune destination addresses sent 0 btc
    destinations = OrderedDict((key, val) for key, val in destinations.items() if val != '0')

    # For each UTXO used as input, we need the txid and vout index to generate a transaction
    inputs = []
    for tx in input_txs:
        utxos = get_utxos(tx, source_address)
        txid = tx["txid"]

        for utxo in utxos:
            inputs.append(OrderedDict([
                ("txid", txid),
                ("vout", int(utxo["n"]))
            ]))

    tx_unsigned_hex = bitcoin_cli_checkoutput(
        "createrawtransaction",
        json.dumps(inputs),
        json.dumps(destinations)).strip()

    return tx_unsigned_hex


def sign_transaction(source_address, keys, redeem_script, unsigned_hex, input_txs):
    """
    Creates a signed transaction
    output => dictionary {"hex": transaction <string>, "complete": <boolean>}

    source_address: <string> input_txs will be filtered for utxos to this source address
    keys: List<string> The private keys you wish to sign with
    redeem_script: <string>
    unsigned_hex: <string> The unsigned transaction, in hex format
    input_txs: List<dict> A list of input transactions to use (bitcoind decoded format)
    """

    # For each UTXO used as input, we need the txid, vout index, scriptPubKey, amount, and redeemScript
    # to generate a signature
    inputs = []
    for tx in input_txs:
        utxos = get_utxos(tx, source_address)
        txid = tx["txid"]
        for utxo in utxos:
            inputs.append({
                "txid": txid,
                "vout": int(utxo["n"]),
                "amount": utxo["value"],
                "scriptPubKey": utxo["scriptPubKey"]["hex"],
                "redeemScript": redeem_script
            })

    signed_tx = bitcoin_cli_json(
        "signrawtransactionwithkey",
        unsigned_hex, json.dumps(keys), json.dumps(inputs))
    return signed_tx


def get_fee_interactive(source_address, keys, destinations, redeem_script, input_txs):
    """
    Returns a recommended transaction fee, given market fee data provided by the user interactively
    Because fees tend to be a function of transaction size, we build the transaction in order to
    recomend a fee.
    return => <Decimal> fee value

    Parameters:
      source_address: <string> input_txs will be filtered for utxos to this source address
      keys: A list of signing keys
      destinations: {address <string>: amount<string>} dictionary mapping destination addresses to amount in BTC
      redeem_script: String
      input_txs: List<dict> List of input transactions in dictionary form (bitcoind decoded format)
      fee_basis_satoshis_per_byte: <int> optional basis for fee calculation
    """

    MAX_FEE = .005  # in btc.  hardcoded limit to protect against user typos

    ensure_bitcoind_running()

    approve = False
    while not approve:
        print("\nEnter fee rate.")
        fee_basis_satoshis_per_byte = int(input("Satoshis per vbyte: "))

        unsigned_tx = create_unsigned_transaction(
            source_address, destinations, redeem_script, input_txs)

        signed_tx = sign_transaction(source_address, keys,
                                     redeem_script, unsigned_tx, input_txs)

        decoded_tx = bitcoin_cli_json("decoderawtransaction", signed_tx["hex"])
        size = decoded_tx["vsize"]

        fee = size * fee_basis_satoshis_per_byte
        fee = satoshi_to_btc(fee)

        if fee > MAX_FEE:
            print("Calculated fee ({}) is too high. Must be under {}".format(fee, MAX_FEE))
        else:
            print("\nBased on the provided rate, the fee will be {} bitcoin.".format(fee))
            confirm = yes_no_interactive()

            if confirm:
                approve = True
            else:
                print("\nFee calculation aborted. Starting over...")

    return fee


################################################################################################
#
# QR code helper functions
#
################################################################################################

def decode_one_qr(filename):
    """
    Decode a QR code from an image file, and return the decoded string.
    """
    zresults = subprocess.run(["zbarimg", "--set", "*.enable=0", "--set", "qr.enable=1",
                              "--quiet", "--raw", filename], check=True, stdout=subprocess.PIPE)
    return zresults.stdout.decode('ascii').strip()


def decode_qr(filenames):
    """
    Decode a (series of) QR codes from a (series of) image file(s), and return the decoded string.
    """
    return ''.join(decode_one_qr(f) for f in filenames)


def write_qr_code(filename, data):
    """
    Write one QR code.
    """
    subprocess.run(["qrencode", "-o", filename, data], check=True)

def write_and_verify_qr_code(name, filename, data):
    """
    Write a QR code and then read it back to try and detect any tricksy malware tampering with it.

    name: <string> short description of the data
    filename: <string> filename for storing the QR code
    data: <string> the data to be encoded
    If data fits in a single QR code, we use filename directly. Otherwise
    we add "-%02d" to each filename; e.g. transaction-01.png transaction-02.png.
    The `qrencode` program can do this directly using "structured symbols" with
    its -S option, but `zbarimg` doesn't recognize those at all. See:
    https://github.com/mchehab/zbar/issues/66
    It's also possible that some mobile phone QR scanners won't recognize such
    codes. So we split it up manually here.
    The theoretical limit of alphanumeric QR codes is 4296 bytes, though
    somehow qrencode can do up to 4302.
    """
    # Remove any stale files, so we don't confuse user if a previous
    # withdrawal created 3 files (or 1 file) and this one only has 2
    base, ext = os.path.splitext(filename)
    for deleteme in glob.glob("{}*{}".format(base, ext)):
        os.remove(deleteme)
    all_upper_case = data.upper() == data
    MAX_QR_LEN = 4200 if all_upper_case else 2800
    if len(data) <= MAX_QR_LEN:
        write_qr_code(filename, data)
        filenames = [filename]
    else:
        idx = 1
        filenames = []
        intdata = data
        while len(intdata) > 0:
            thisdata = intdata[0:MAX_QR_LEN]
            intdata = intdata[MAX_QR_LEN:]
            thisfile = "{}-{:02d}{}".format(base, idx, ext)
            filenames.append(thisfile)
            write_qr_code(thisfile, thisdata)
            idx += 1

    qrdata = decode_qr(filenames)
    if qrdata != data:
        print("********************************************************************")
        print("WARNING: {} QR code could not be verified properly. This could be a sign of a security breach.".format(name))
        print("********************************************************************")

    print("QR code for {0} written to {1}".format(name, ','.join(filenames)))


################################################################################################
#
# User sanity checking
#
################################################################################################

def yes_no_interactive():
    def confirm_prompt():
        return input("Confirm? (y/n): ")

    confirm = confirm_prompt()

    while True:
        if confirm.upper() == "Y":
            return True
        if confirm.upper() == "N":
            return False
        else:
            print("You must enter y (for yes) or n (for no).")
            confirm = confirm_prompt()

def safety_checklist():

    checks = [
        "Are you running this on a computer WITHOUT a network connection of any kind?",
        "Have the wireless cards in this computer been physically removed?",
        "Are you running on battery power?",
        "Are you running on an operating system booted from a USB drive?",
        "Is your screen hidden from view of windows, cameras, and other people?",
        "Are smartphones and all other nearby devices turned off and in a Faraday bag?"]

    for check in checks:
        answer = input(check + " (y/n)?")
        if answer.upper() != "Y":
            print("\n Safety check failed. Exiting.")
            sys.exit()


################################################################################################
#
# Main "entropy" function
#
################################################################################################


def unchunk(string):
    """
    Remove spaces in string
    """
    return string.replace(" ", "")


def chunk_string(string, length):
    """
    Splits a string into chunks of [length] characters, for easy human readability
    Source: https://stackoverflow.com/a/18854817/11031317
    """
    return (string[0+i:length+i] for i in range(0, len(string), length))


def entropy(n, length):
    """
    Generate n random strings for the user from /dev/random
    """
    safety_checklist()

    print("\n\n")
    print("Making {} random data strings....".format(n))
    print("If strings don't appear right away, please continually move your mouse cursor. These movements generate entropy which is used to create random data.\n")

    idx = 0
    while idx < n:
        seed = subprocess.check_output(
            "xxd -l {} -p /dev/random".format(length), shell=True)
        idx += 1
        seed = seed.decode('ascii').replace('\n', '')
        print("Computer entropy #{0}: {1}".format(idx, " ".join(chunk_string(seed, 4))))


################################################################################################
#
# Main "deposit" function
#
################################################################################################

def deposit_interactive(m, n, dice_seed_length=62, rng_seed_length=20, p2wsh=False):
    """
    Generate data for a new cold storage address (private keys, address, redemption script)
    m: <int> number of multisig keys required for withdrawal
    n: <int> total number of multisig keys
    dice_seed_length: <int> minimum number of dice rolls required
    rng_seed_length: <int> minimum length of random seed required
    p2wsh: if True, generate p2wsh instead of p2wsh-in-p2sh
    """

    safety_checklist()
    ensure_bitcoind_running()
    require_minimum_bitcoind_version(220000) # decoderawtransaction output changed in v22.0.0

    print("\n")
    print("Creating {0}-of-{1} cold storage address.\n".format(m, n))

    keys = []

    while len(keys) < n:
        index = len(keys) + 1
        print("\nCreating private key #{}".format(index))

        dice_seed_string = read_dice_seed_interactive(dice_seed_length)
        dice_seed_hash = hash_sha256(dice_seed_string)

        rng_seed_string = read_rng_seed_interactive(rng_seed_length)
        rng_seed_hash = hash_sha256(rng_seed_string)

        # back to hex string
        hex_private_key = xor_hex_strings(dice_seed_hash, rng_seed_hash)
        WIF_private_key = hex_private_key_to_WIF_private_key(hex_private_key)
        keys.append(WIF_private_key)

    print("Private keys created.")
    print("Generating {0}-of-{1} cold storage address...\n".format(m, n))

    addresses = [get_address_for_wif_privkey(key) for key in keys]
    address_type = 'bech32' if p2wsh else 'p2sh-segwit'
    results = addmultisigaddress(m, addresses, address_type)

    print("Private keys:")
    for idx, key in enumerate(keys):
        print("Key #{0}: {1}".format(idx + 1, key))

    print("\nCold storage address:")
    print("{}".format(results["address"]))

    print("\nRedemption script:")
    print("{}".format(results["redeemScript"]))
    print("")

    write_and_verify_qr_code("cold storage address", "address.png", results["address"])
    write_and_verify_qr_code("redemption script", "redemption.png",
                       results["redeemScript"])


################################################################################################
#
# Main "withdraw" function
#
################################################################################################

def withdraw_interactive():
    """
    Construct and sign a transaction to withdaw funds from cold storage
    All data required for transaction construction is input at the terminal
    """

    safety_checklist()
    ensure_bitcoind_running()
    require_minimum_bitcoind_version(220000) # decoderawtransaction output changed in v22.0.0

    approve = False

    while not approve:
        addresses = OrderedDict()

        print("\nYou will need to enter several pieces of information to create a withdrawal transaction.")
        print("\n\n*** PLEASE BE SURE TO ENTER THE CORRECT DESTINATION ADDRESS ***\n")

        source_address = input("\nSource cold storage address: ")
        addresses[source_address] = 0

        redeem_script = input("\nRedemption script for source cold storage address: ")

        dest_address = input("\nDestination address: ")
        addresses[dest_address] = 0

        num_tx = int(input("\nHow many unspent transactions will you be using for this withdrawal? "))

        txs = []
        utxos = []
        utxo_sum = Decimal(0).quantize(SATOSHI_PLACES)

        while len(txs) < num_tx:
            print("\nPlease paste raw transaction #{} (hexadecimal format) with unspent outputs at the source address".format(len(txs) + 1))
            print("OR")
            print("input a filename located in the current directory which contains the raw transaction data")
            print("(If the transaction data is over ~4000 characters long, you _must_ use a file.):")

            hex_tx = input()
            if os.path.isfile(hex_tx):
                hex_tx = open(hex_tx).read().strip()

            tx = bitcoin_cli_json("decoderawtransaction", hex_tx)
            txs.append(tx)
            utxos += get_utxos(tx, source_address)

        if len(utxos) == 0:
            print("\nTransaction data not found for source address: {}".format(source_address))
            sys.exit()
        else:
            print("\nTransaction data found for source address.")

            for utxo in utxos:
                value = Decimal(utxo["value"]).quantize(SATOSHI_PLACES)
                utxo_sum += value

            print("TOTAL unspent amount for this raw transaction: {} BTC".format(utxo_sum))

        print("\nHow many private keys will you be signing this transaction with? ")
        key_count = int(input("#: "))

        keys = []
        while len(keys) < key_count:
            key = input("Key #{0}: ".format(len(keys) + 1))
            keys.append(key)

        ###### fees, amount, and change #######

        input_amount = utxo_sum
        fee = get_fee_interactive(
            source_address, keys, addresses, redeem_script, txs)
        # Got this far
        if fee > input_amount:
            print("ERROR: Your fee is greater than the sum of your unspent transactions.  Try using larger unspent transactions. Exiting...")
            sys.exit()

        print("\nPlease enter the decimal amount (in bitcoin) to withdraw to the destination address.")
        print("\nExample: For 2.3 bitcoins, enter \"2.3\".")
        print("\nAfter a fee of {0}, you have {1} bitcoins available to withdraw.".format(fee, input_amount - fee))
        print("\n*** Technical note for experienced Bitcoin users:  If the withdrawal amount & fee are cumulatively less than the total amount of the unspent transactions, the remainder will be sent back to the same cold storage address as change. ***\n")
        withdrawal_amount = input(
            "Amount to send to {0} (leave blank to withdraw all funds stored in these unspent transactions): ".format(dest_address))
        if withdrawal_amount == "":
            withdrawal_amount = input_amount - fee
        else:
            withdrawal_amount = Decimal(withdrawal_amount).quantize(SATOSHI_PLACES)

        if fee + withdrawal_amount > input_amount:
            print("Error: fee + withdrawal amount greater than total amount available from unspent transactions")
            raise Exception("Output values greater than input value")

        change_amount = input_amount - withdrawal_amount - fee

        # less than a satoshi due to weird floating point imprecision
        if change_amount < 1e-8:
            change_amount = 0

        if change_amount > 0:
            print("{0} being returned to cold storage address address {1}.".format(change_amount, source_address))

        addresses[dest_address] = str(withdrawal_amount)
        addresses[source_address] = str(change_amount)

        # check data
        print("\nIs this data correct?")
        print("*** WARNING: Incorrect data may lead to loss of funds ***\n")

        print("{0} BTC in unspent supplied transactions".format(input_amount))
        for address, value in addresses.items():
            if address == source_address:
                print("{0} BTC going back to cold storage address {1}".format(value, address))
            else:
                print("{0} BTC going to destination address {1}".format(value, address))
        print("Fee amount: {0}".format(fee))
        print("\nSigning with private keys: ")
        for key in keys:
            print("{}".format(key))

        print("\n")
        confirm = yes_no_interactive()

        if confirm:
            approve = True
        else:
            print("\nProcess aborted. Starting over....")

    #### Calculate Transaction ####
    print("\nCalculating transaction...\n")

    unsigned_tx = create_unsigned_transaction(
        source_address, addresses, redeem_script, txs)

    signed_tx = sign_transaction(source_address, keys,
                                 redeem_script, unsigned_tx, txs)

    print("\nSufficient private keys to execute transaction?")
    print(signed_tx["complete"])

    print("\nRaw signed transaction (hex):")
    print(signed_tx["hex"])

    print("\nTransaction fingerprint (md5):")
    print(hash_md5(signed_tx["hex"]))

    write_and_verify_qr_code("transaction", "transaction.png", signed_tx["hex"].upper())


################################################################################################
#
# main function
#
# Show help, or execute one of the three main routines: entropy, deposit, withdraw
#
################################################################################################

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('program', choices=[
                        'entropy', 'create-deposit-data', 'create-withdrawal-data', 'start-bitcoind', 'test-qr-code'])

    parser.add_argument("--num-keys", type=int,
                        help="The number of keys to create random entropy for", default=1)
    parser.add_argument("-d", "--dice", type=int,
                        help="The minimum number of dice rolls to use for entropy when generating private keys (default: 62)", default=62)
    parser.add_argument("-r", "--rng", type=int,
                        help="Minimum number of 8-bit bytes to use for computer entropy when generating private keys (default: 20)", default=20)
    parser.add_argument(
        "-m", type=int, help="Number of signing keys required in an m-of-n multisig address creation (default m-of-n = 1-of-2)", default=1)
    parser.add_argument(
        "-n", type=int, help="Number of total keys required in an m-of-n multisig address creation (default m-of-n = 1-of-2)", default=2)
    parser.add_argument(
        "--p2wsh", action="store_true", help="Generate p2wsh (native segwit) deposit address, instead of p2wsh-in-p2sh")
    parser.add_argument('--testnet', type=int, help=argparse.SUPPRESS)
    parser.add_argument('-v', '--verbose', action='store_true', help='increase output verbosity')
    args = parser.parse_args()

    verbose_mode = args.verbose

    global cli_args, wif_prefix
    cli_args = ["-testnet", "-rpcport={}".format(args.testnet), "-datadir=bitcoin-test-data"] if args.testnet else []
    wif_prefix = "EF" if args.testnet else "80"

    if args.program == "entropy":
        entropy(args.num_keys, args.rng)

    if args.program == "create-deposit-data":
        deposit_interactive(args.m, args.n, args.dice, args.rng, args.p2wsh)

    if args.program == "create-withdrawal-data":
        withdraw_interactive()

    if args.program == "start-bitcoind":
        ensure_bitcoind_running()

    if args.program == "test-qr-code":
        write_and_verify_qr_code("abc", "abc.png", "abcdef")
