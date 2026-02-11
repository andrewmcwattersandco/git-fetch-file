#!/bin/sh
printf '' > .gitignore
langs="Python Go"

for lang in ${langs}
do
    curl -o - https://raw.githubusercontent.com/github/gitignore/main/${lang}.gitignore >> .gitignore
done

# go.mod
touch "$TMPDIR"/.gitignore
grep -v '^\*.mod' .gitignore > "$TMPDIR"/.gitignore
mv "$TMPDIR"/.gitignore .gitignore
