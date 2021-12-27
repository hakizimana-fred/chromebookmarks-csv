import os
import platform
import json
import csv

def main():
    if not platform.system().startswith('Linux'):
        pass
    bookmarks_path = f"{os.path.expanduser('~')}/.config/google-chrome/Default/Bookmarks"

    bookmarks = []
    if os.path.exists(bookmarks_path):
        with open(bookmarks_path, 'r', encoding='utf-8') as f:
            bookmarks_data = json.load(f)
            for data in bookmarks_data["roots"]["bookmark_bar"]["children"]:
                # book_mark object
                bookmark = {'Title': data['name'], 'URL': data['url']}
                if bookmark not in bookmarks:
                    bookmarks.append(bookmark)
    else:
        print('Could not trace path')
    csvParser(bookmarks)


def csvParser(bookmarks):
    keys = ['Title', 'URL']
    with open("bookmarks.csv", "w") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        writer.writerows(bookmarks)


if __name__ == '__main__':
    main()