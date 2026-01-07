#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import ldb
import getpass
from samba.auth import system_session
from samba.credentials import Credentials
from samba.param import LoadParm
from samba.samdb import SamDB
from samba.dsdb import UF_NORMAL_ACCOUNT, UF_DONT_EXPIRE_PASSWD
from ldb import LdbError, SCOPE_SUBTREE, FLAG_MOD_REPLACE
from rich import print
from rich.progress import track

DOMAIN_DN = "DC=example,DC=org"
LDAP_URL = "ldap://127.0.0.1:389"
ADMIN_USER = "Administrator"
UAC_ENABLE = UF_NORMAL_ACCOUNT | UF_DONT_EXPIRE_PASSWD

def get_samba_connection(password):
    lp = LoadParm()
    creds = Credentials()
    creds.guess(lp)
    creds.set_username(ADMIN_USER)
    creds.set_password(password)
    return SamDB(url=LDAP_URL, session_info=system_session(), credentials=creds, lp=lp)

def process_organization(samdb, elt):
    ou_name = elt["cn"]
    base_ou_dn = f"OU={ou_name},OU=Customers,{DOMAIN_DN}"
    
    ous_to_create = [
        base_ou_dn,
        f"OU=Groups,{base_ou_dn}",
        f"OU=Users,{base_ou_dn}"
    ]

    errors = 0
    for ou_dn in ous_to_create:
        try:
            samdb.create_ou(ou_dn)
            print(f"[[bold italic green]Task OK[/]] | [bold white]Création OU [purple bold]{ou_dn.split(',')[0]}[/] .[/]")
        except LdbError as e:
            (num, msg) = e.args
            if num != 68:
                errors += 1
                print(f"[[bold italic red]Error[/]]   | Erreur sur {ou_dn}: {msg}")
    return errors

def add_user_to_group(samdb, user_dn, group_name):
    try:
        samdb.add_remove_group_members(group_name, [str(user_dn)], add_members_operation=True)
        print(f"[[bold italic green]Task OK[/]] | [bold white]Ajout au groupe [purple bold]{group_name}[/].[/]")
        return 0
    except Exception:
        return 0

def process_user(samdb, elt):
    errors = 0
    uid = elt["uid"]
    dn = elt["dn"]
    
    try:
        nom2domain = dn.split('cn=')[1].split(',')[0]
    except IndexError:
        print(f"[[bold red]Error[/]]    | Domaine invalide pour {uid}")
        return 1

    full_uid = f"{uid}_{nom2domain}"
    target_dn = f"CN={full_uid},OU=Users,OU={nom2domain},OU=Collectivites,{DOMAIN_DN}"
    
    exists = False
    try:
        samdb.search(base=target_dn, scope=ldb.SCOPE_BASE)
        exists = True
        print(f"[[bold italic yellow]Info[/]]    | [bold white]L'utilisateur [bold purple]{full_uid}[/] existe déjà (Update).[/]")
    except LdbError:
        exists = False

    if not exists:
        try:
            samdb.newuser(username=full_uid, password=elt["userpassword"])
            temp_dn = f"CN={full_uid},CN=Users,{DOMAIN_DN}"
            samdb.rename(temp_dn, target_dn)
            print(f"[[bold italic green]Task OK[/]] | [bold white]Utilisateur [bold purple]{full_uid}[/] créé et déplacé.[/]")
        except LdbError as e:
            print(f"[[bold italic red]Error[/]]   | Erreur création user: {e}")
            return 1

    msg = ldb.Message()
    msg.dn = ldb.Dn(samdb, target_dn)
    
    if elt.get("uid"):
        msg["uid"] = ldb.MessageElement(uid, FLAG_MOD_REPLACE, "uid")
    if elt.get("displayname"):
        msg["displayName"] = ldb.MessageElement(elt["displayname"], FLAG_MOD_REPLACE, "displayName")
    if elt.get("mail"):
        msg["mail"] = ldb.MessageElement(elt["mail"], FLAG_MOD_REPLACE, "mail")
    if elt.get("sn"):
        msg["sn"] = ldb.MessageElement(elt["sn"], FLAG_MOD_REPLACE, "sn")
    if elt.get("givenname"):
        msg["givenName"] = ldb.MessageElement(elt["givenname"], FLAG_MOD_REPLACE, "givenName")
    
    msg["userAccountControl"] = ldb.MessageElement(str(UAC_ENABLE), FLAG_MOD_REPLACE, "userAccountControl")

    try:
        samdb.modify(msg)
    except LdbError as e:
        errors += 1
        print(f"[[bold italic red]Task Failed[/]] | Erreur modif attributs: {e}")

    user_dn_obj = ldb.Dn(samdb, target_dn)
    add_user_to_group(samdb, user_dn_obj, "generic_app_group_1")
    add_user_to_group(samdb, user_dn_obj, "generic_app_group_2")
    
    return errors

def import_from_json(json_file):
    try:
        password = getpass.getpass("Mot de passe Administrator : ")
    except Exception:
        sys.exit(1)

    try:
        samdb = get_samba_connection(password)
    except Exception as e:
        print(f"[bold red]Impossible de se connecter à Samba : {e}[/]")
        sys.exit(1)

    total_errors = 0

    with open(json_file, 'r', encoding='utf-8') as fichier:
        dico = json.load(fichier)
        
        for elt in track(dico, description="[green]Importation en cours ..[/]"):
            typeofobject = elt.get("objectclass")
            
            if isinstance(typeofobject, list):
                if "groupOfNames" in typeofobject:
                    total_errors += process_organization(samdb, elt)
                elif "person" in typeofobject or "inetOrgPerson" in typeofobject:
                    total_errors += process_user(samdb, elt)
            else:
                if typeofobject == "groupOfNames":
                    total_errors += process_organization(samdb, elt)
                elif typeofobject in ["person", "organizationalPerson", "inetOrgPerson"]:
                    total_errors += process_user(samdb, elt)
            
    print("[bold]______________________________________________________________________________________[/]\n")
    print(f"\nTerminé avec {total_errors} erreurs.") 

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("[bold red]Erreur:[/bold red] fichier JSON en argument.")
        sys.exit(1)
        
    import_from_json(sys.argv[1])
