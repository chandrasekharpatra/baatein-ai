ALTER TABLE users ADD COLUMN persona TEXT NOT NULL DEFAULT 'casual';

ALTER TABLE users ADD COLUMN address_as TEXT;

ALTER TABLE users ADD COLUMN pace TEXT CHECK (pace IN ('slow', 'normal', 'fast'));

ALTER TABLE users ADD COLUMN verbosity TEXT CHECK (verbosity IN ('short', 'normal', 'detailed'));

ALTER TABLE users ADD COLUMN fewer_questions INTEGER NOT NULL DEFAULT 0;
