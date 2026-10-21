"""Publie une vidéo Smooth Animations sur TikTok, Instagram et YouTube par l'API Buffer.

La vidéo est poussée dans ce dépôt public, puis Buffer la récupère par son lien raw.
Par défaut, crée des BROUILLONS. Il faut --publier pour une mise en ligne immédiate.

Exemples :
  python publier_buffer.py ../smooth-pret-a-poster/duel_son_D.mp4 --textes ../smooth-pret-a-poster/duel.txt
  python publier_buffer.py video.mp4 --titre "It Grows Every Time" --legende "..." --hashtags "#satisfying #asmr"
  python publier_buffer.py video.mp4 --textes x.txt --publier --reseaux youtube,tiktok

Clé : C:\\Users\\titou\\.secrets\\buffer_api_key.txt (jamais affichée, jamais copiée ici).
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEPOT = pathlib.Path(__file__).resolve().parent
URL_RAW = "https://raw.githubusercontent.com/TN-Studio/smooth-videos/main/videos/"
CLE = pathlib.Path(r"C:\Users\titou\.secrets\buffer_api_key.txt")
CANAUX = {
    "tiktok": "6ad77009778b240e2f69c253",
    "youtube": "6ad76fd5778b240e2f69c0bb",
    "instagram": "6ad76f88778b240e2f69be36",
}
TAILLE_MAX = 99 * 1024 * 1024  # GitHub refuse les fichiers de plus de 100 Mio
DEBIT_MIN = 12_000_000  # en dessous, la qualité se voit (demande de Titouan)
CATEGORIE_YOUTUBE = "24"  # Entertainment


def gql(requete, variables=None):
    corps = json.dumps({"query": requete, "variables": variables or {}}).encode()
    req = urllib.request.Request("https://api.buffer.com", data=corps, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + CLE.read_text().strip(),
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        return {"errors": [{"message": f"HTTP {e.code} {e.read().decode()[:300]}"}]}


def lire_textes(chemin):
    """Lit un fichier au format de smooth-pret-a-poster (Titre YouTube :, Légende :, Hashtags :, Commentaire à épingler :)."""
    champs = {}
    for ligne in pathlib.Path(chemin).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(Titre YouTube|Titre|Légende|Hashtags|Commentaire à épingler)\s*:\s*(.+)", ligne, re.I)
        if m:
            noms = {"titre youtube": "Titre YouTube", "titre": "Titre YouTube", "légende": "Légende", "hashtags": "Hashtags",
                    "commentaire à épingler": "Commentaire à épingler"}
            champs[noms[m.group(1).lower()]] = m.group(2).strip()
    return {
        "titre": champs.get("Titre YouTube"),
        "legende": champs.get("Légende"),
        "hashtags": champs.get("Hashtags", ""),
        "commentaire": champs.get("Commentaire à épingler"),
    }


def duree(video):
    sortie = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True)
    return float(sortie.stdout.strip())


def preparer_video(source, nom):
    """Copie la vidéo dans videos/, en la recompressant sous 95 Mo si besoin."""
    cible = DEPOT / "videos" / nom
    cible.parent.mkdir(exist_ok=True)
    if cible.exists() and cible.stat().st_mtime >= source.stat().st_mtime:
        return cible  # déjà préparée, la source n'a pas changé depuis
    if source.stat().st_size <= TAILLE_MAX:
        shutil.copyfile(source, cible)
        return cible
    # Débit visé pour tenir dans 94 Mio (marge de 5 % : le 2 passes déborde), son compris
    debit = int(94 * 8 * 1024 * 1024 / duree(source)) - 160_000
    if debit < DEBIT_MIN:
        print(f"ATTENTION : {debit // 1000} kbit/s seulement, sous les 12 Mb/s voulus (vidéo trop longue pour GitHub)")
    print(f"Vidéo de {source.stat().st_size / 1e6:.0f} Mo : recompression à {debit // 1000} kbit/s")
    journal = DEPOT / "videos" / "passe"
    commun = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-c:v", "libx264", "-preset", "slow",
              "-b:v", str(debit), "-passlogfile", str(journal)]
    subprocess.run(commun + ["-pass", "1", "-an", "-f", "mp4", "NUL"], check=True)
    subprocess.run(commun + ["-pass", "2", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                             str(cible)], check=True)
    for f in cible.parent.glob("passe*"):
        f.unlink()
    if cible.stat().st_size > TAILLE_MAX:
        sys.exit(f"Toujours trop lourde après recompression : {cible.stat().st_size / 1e6:.0f} Mo")
    return cible


def pousser(fichier):
    rel = fichier.relative_to(DEPOT).as_posix()
    subprocess.run(["git", "-C", str(DEPOT), "add", rel], check=True)
    if subprocess.run(["git", "-C", str(DEPOT), "diff", "--cached", "--quiet"]).returncode == 0:
        print("Vidéo déjà dans le dépôt, rien à pousser")
        return
    subprocess.run(["git", "-C", str(DEPOT), "commit", "-q", "-m", f"Ajoute {fichier.name}"], check=True)
    for essai in range(3):  # GitHub coupe parfois les gros envois
        if subprocess.run(["git", "-C", str(DEPOT), "push", "-q"]).returncode == 0:
            return
        time.sleep(10)
    sys.exit("Échec de l'envoi sur GitHub après 3 essais")


def attendre_en_ligne(url, taille, essais=30):
    for _ in range(essais):
        try:
            req = urllib.request.Request(url, method="HEAD")
            reponse = urllib.request.urlopen(req, timeout=30)
            if int(reponse.headers.get("Content-Length", 0)) == taille:
                return
        except urllib.error.URLError:
            pass
        time.sleep(5)
    sys.exit(f"La vidéo n'est pas accessible publiquement : {url}")


CREER = """
mutation Creer($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}"""


def creer_post(reseau, url, textes, publier, heure=None):
    texte = textes["legende"]
    hashtags = textes["hashtags"]
    if reseau == "youtube" and "#shorts" not in hashtags:
        hashtags = "#shorts " + hashtags
    if hashtags:
        texte = f"{texte}\n\n{hashtags}"
    metadata = {}
    if reseau == "youtube":
        metadata["youtube"] = {"title": textes["titre"], "categoryId": CATEGORIE_YOUTUBE,
                               "madeForKids": False, "privacy": "public"}
    elif reseau == "instagram":
        # Le premier commentaire est réservé aux offres payantes de Buffer : à épingler à la main
        metadata["instagram"] = {"type": "reel", "shouldShareToFeed": True}
    entree = {
        "channelId": CANAUX[reseau],
        "text": texte,
        "assets": [{"video": {"url": url}}],
        "metadata": metadata,
        "schedulingType": "automatic",
        "needsApproval": False,
        "mode": "shareNow" if publier else "addToQueue",
    }
    if heure:
        entree["mode"] = "customScheduled"
        entree["dueAt"] = heure
    elif not publier:
        entree["saveToDraft"] = True
    return gql(CREER, {"input": entree})


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("--textes", help="fichier .txt au format de smooth-pret-a-poster")
    p.add_argument("--titre")
    p.add_argument("--legende")
    p.add_argument("--hashtags")
    p.add_argument("--commentaire")
    p.add_argument("--reseaux", default="tiktok,instagram,youtube")
    p.add_argument("--publier", action="store_true", help="publie tout de suite au lieu de créer des brouillons")
    p.add_argument("--heure", help="programme à cette heure de Paris, ex. 2026-10-20T21:00")
    a = p.parse_args()
    heure = None
    if a.heure:
        from datetime import datetime
        # Heure locale du PC (Paris) ; zoneinfo n'a pas de base de fuseaux sur ce Windows
        heure = datetime.fromisoformat(a.heure).astimezone().isoformat()

    textes = lire_textes(a.textes) if a.textes else {}
    for cle in ("titre", "legende", "hashtags", "commentaire"):
        if getattr(a, cle):
            textes[cle] = getattr(a, cle)
    if not textes.get("legende"):
        sys.exit("Il manque la légende (--legende ou --textes)")
    reseaux = [r.strip() for r in a.reseaux.split(",") if r.strip()]
    if "youtube" in reseaux and not textes.get("titre"):
        sys.exit("Il manque le titre YouTube (--titre ou --textes)")

    source = pathlib.Path(a.video).resolve()
    nom = re.sub(r"[^a-z0-9_-]+", "-", source.stem.lower()).strip("-") + ".mp4"
    fichier = preparer_video(source, nom)
    pousser(fichier)
    url = URL_RAW + nom
    attendre_en_ligne(url, fichier.stat().st_size)
    print(f"Vidéo en ligne : {url}")

    mode = f"programmé {a.heure}" if heure else ("PUBLICATION" if a.publier else "brouillon")
    for reseau in reseaux:
        r = creer_post(reseau, url, textes, a.publier, heure)
        resultat = ((r.get("data") or {}).get("createPost") or {})
        if "post" in resultat:
            print(f"{reseau} : {mode} créé ({resultat['post']['status']}, id {resultat['post']['id']})")
        else:
            erreur = resultat.get("message") or r.get("errors")
            print(f"{reseau} : ÉCHEC {erreur}")


if __name__ == "__main__":
    main()
